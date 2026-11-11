import logging
import sqlite3

import pytest

from lyra.database.connection import DatabaseConnection, integrity_ok
from lyra.database.errors import DatabaseCorruptError


def _database_with_a_corrupt_page(db_path):
    """Build a readable DB whose page 2 has an invalid b-tree header.

    SQLite still opens this file, but ``PRAGMA integrity_check`` reports the
    damage: that report, and nothing else, is what may mean "corrupt".
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA page_size=1024")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, s TEXT)")
    conn.executemany("INSERT INTO t (s) VALUES (?)", [("x" * 300,) for _ in range(400)])
    conn.commit()
    conn.close()

    data = bytearray(db_path.read_bytes())
    page_size = int.from_bytes(data[16:18], "big")
    # The first table b-tree page after the schema page: 0x05 interior, 0x0d leaf.
    page = next(
        index
        for index in range(1, len(data) // page_size)
        if data[index * page_size] in (0x05, 0x0D)
    )
    data[page * page_size] = 0x00  # an invalid b-tree page type
    db_path.write_bytes(bytes(data))


def test_two_instances_do_not_share_path(tmp_path):
    a = DatabaseConnection(tmp_path / "a.db")
    b = DatabaseConnection(tmp_path / "b.db")
    assert a.path != b.path
    assert a is not b


def test_constructor_does_not_touch_disk(tmp_path):
    db_path = tmp_path / "nested" / "never.db"
    DatabaseConnection(db_path)
    assert not db_path.parent.exists()
    assert not db_path.exists()


def test_initialize_enables_wal_and_busy_timeout(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_reader_not_blocked_by_open_writer_transaction(tmp_path):
    """Semantik WAL: reader tidak menunggu writer yang transaksinya belum commit."""
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as w:
        w.execute("CREATE TABLE x (v INTEGER)")
        w.commit()
        w.isolation_level = None
        w.execute("BEGIN IMMEDIATE")
        w.execute("INSERT INTO x VALUES (1)")
        with conn.get_connection() as r:
            assert r.execute("SELECT COUNT(*) FROM x").fetchone()[0] == 0
        w.execute("COMMIT")


def test_initialize_raises_corrupt_error_and_leaves_file_untouched(tmp_path):
    """A corrupt DB must not crash startup with a silent rebuild.

    Repair is explicit (``lyra --repair-db``); until the operator runs it the
    file stays byte-for-byte where it is, because renaming it destroys evidence.
    """
    db_path = tmp_path / "garbage.db"
    original = b"\x00\x01 not a database at all \x00"
    db_path.write_bytes(original)

    with pytest.raises(DatabaseCorruptError) as excinfo:
        DatabaseConnection(db_path).initialize()

    message = str(excinfo.value)
    assert str(db_path) in message
    assert "--repair-db" in message
    assert db_path.read_bytes() == original
    assert [p.name for p in tmp_path.iterdir()] == [db_path.name]


def test_locked_database_is_not_renamed(tmp_path):
    """A lock is a concurrency condition, never corruption: the file stays put.

    The database has no schema yet, so the exclusive lock blocks the ``CREATE
    TABLE`` that ``initialize()`` must run -- the lock error has to surface, and
    the live file has to survive it.
    """
    db_path = tmp_path / "live.db"
    sqlite3.connect(db_path).close()  # an empty database, no schema yet

    holder = sqlite3.connect(db_path, timeout=1)
    holder.isolation_level = None
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            DatabaseConnection(db_path).initialize()
        assert not list(tmp_path.glob("*.corrupt*"))
        assert db_path.exists()
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_integrity_ok_is_decided_by_integrity_check(tmp_path):
    """Only ``PRAGMA integrity_check`` may call a file corrupt."""
    healthy = tmp_path / "healthy.db"
    DatabaseConnection(healthy).initialize()
    assert integrity_ok(healthy)

    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database at all" * 50)
    assert not integrity_ok(garbage)  # raises instead of returning a row

    damaged = tmp_path / "damaged.db"
    _database_with_a_corrupt_page(damaged)
    conn = sqlite3.connect(damaged)
    try:
        reported = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    assert reported != [("ok",)]  # precondition: the fixture is really damaged
    assert not integrity_ok(damaged)


def test_journal_mode_is_checked_not_ignored(tmp_path, caplog):
    """A silent non-WAL result must be reported: the task's concurrency guarantee
    depends on the file really being in WAL mode."""
    db = DatabaseConnection(tmp_path / "t.db")
    mem = sqlite3.connect(":memory:")  # an in-memory DB can never be WAL
    try:
        with caplog.at_level(logging.WARNING, logger="lyra.database.connection"):
            mode = db._enable_wal(mem)
    finally:
        mem.close()

    assert mode.lower() != "wal"  # precondition: a genuine non-WAL answer
    assert "journal_mode" in caplog.text
    assert "wal" in caplog.text.lower()


def test_get_connection_does_not_leak_connection_when_pragma_fails(tmp_path, monkeypatch):
    """A raise while applying PRAGMAs must still close the connection it opened."""
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"\x00\x01 not a database at all \x00")
    db = DatabaseConnection(db_path)

    created = []
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        created.append(conn)
        return conn

    monkeypatch.setattr("lyra.database.connection.sqlite3.connect", spy)

    with pytest.raises(sqlite3.DatabaseError):
        with db.get_connection():
            pass

    assert created, "expected the failing connection to have been opened"
    for conn in created:
        # ProgrammingError == "Cannot operate on a closed database".
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
