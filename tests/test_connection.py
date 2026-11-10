import logging
import sqlite3

import pytest

from lyra.database.connection import DatabaseConnection


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


def test_initialize_recovers_from_garbage_database(tmp_path):
    """A corrupt DB file must not crash startup, and the schema must stay usable."""
    db_path = tmp_path / "garbage.db"
    db_path.write_bytes(b"\x00\x01 not a database at all \x00")
    db = DatabaseConnection(db_path)

    db.initialize()  # must not raise

    with db.get_connection() as c:
        c.execute(
            "INSERT INTO artists (artist_name, added_at) VALUES (?, ?)",
            ("some-artist", "2026-11-11"),
        )
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM artists").fetchone()[0] == 1
        # WAL is a file property: it must hold for the rebuilt file too.
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_corrupt_database_file_is_backed_up_before_rebuild(tmp_path):
    """Scoped to today's auto-rename. Task 2 replaces it with an explicit --repair-db,
    and should update or delete this test rather than the recovery mandate above."""
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"\x00\x01 not a database at all \x00")

    DatabaseConnection(db_path).initialize()

    backups = [p.name for p in tmp_path.iterdir() if p.name.startswith("corrupt.corrupt")]
    assert len(backups) == 1


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
