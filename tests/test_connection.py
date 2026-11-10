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
