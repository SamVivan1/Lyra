"""Explicit repair: a lock is not corruption and nothing is renamed automatically."""

import os
import sqlite3
import sys

import pytest

import lyra.main as main_module
from lyra.config import Config
from lyra.database.connection import DatabaseConnection
from lyra.database.errors import DatabaseFileError
from lyra.database.maintenance import backup_database, repair_database

GARBAGE = b"this is not a database at all"


def test_lock_is_not_treated_as_corruption(tmp_path):
    """A lock is a concurrency condition, never corruption.

    The file is switched to the rollback journal on purpose: in WAL mode an open
    writer does not block readers (that is the guarantee Task 1 installed), so a
    WAL-mode lock would not fail startup at all. In rollback-journal mode the
    exclusive lock really does block the second connection, which is the case
    this test exists for.
    """
    db = tmp_path / "live.db"
    conn = DatabaseConnection(db)
    conn.initialize()
    holder = sqlite3.connect(db, timeout=1)
    holder.isolation_level = None
    holder.execute("PRAGMA journal_mode=DELETE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            DatabaseConnection(db).initialize()
        assert not list(tmp_path.glob("*.corrupt*")), "DB tidak boleh di-rename saat lock"
        assert db.exists()
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_repair_creates_backup_and_reports_lost_rows(tmp_path):
    db = tmp_path / "bad.db"
    db.write_bytes(b"this is not a database at all" * 100)
    report = repair_database(db)
    assert report.backup_path.exists()
    assert report.integrity_ok
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 0


def test_repair_keeps_the_corrupt_bytes_in_the_backup(tmp_path):
    """The operator's evidence must survive: the backup is an exact copy."""
    db_path = tmp_path / "bad.db"
    original = GARBAGE * 100
    db_path.write_bytes(original)

    report = repair_database(db_path)

    assert report.backup_path.read_bytes() == original
    assert db_path.read_bytes() != original
    assert set(report.tables) == {"artists", "local_library", "tracked_tracks"}
    assert all(rows == 0 for rows in report.tables.values())


def test_repair_drops_stale_wal_and_shm_sidecars(tmp_path):
    """A database that died in WAL mode must not hand its old WAL to the new file."""
    db_path = tmp_path / "bad.db"
    db_path.write_bytes(GARBAGE * 100)
    stale_wal = db_path.with_name(f"{db_path.name}-wal")
    stale_shm = db_path.with_name(f"{db_path.name}-shm")
    stale_wal.write_bytes(GARBAGE)
    stale_shm.write_bytes(GARBAGE)

    repair_database(db_path)

    assert not stale_wal.exists()
    assert not stale_shm.exists()
    # A replayed stale WAL would make this fail to open; the rebuilt file is whole.
    with sqlite3.connect(db_path) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT COUNT(*) FROM artists").fetchone()[0] == 0


def test_backup_database_never_reuses_a_name(tmp_path):
    db_path = tmp_path / "live.db"
    DatabaseConnection(db_path).initialize()

    first = backup_database(db_path)
    second = backup_database(db_path)

    assert first != second
    assert first.exists() and second.exists()
    assert first.read_bytes() == db_path.read_bytes()


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory mode bits",
)
def test_initialize_reports_filesystem_failure_clearly(tmp_path):
    """A read-only directory must not escape as a raw OSError."""
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        with pytest.raises(DatabaseFileError):
            DatabaseConnection(readonly / "nested" / "db.db").initialize()
    finally:
        readonly.chmod(0o700)


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory mode bits",
)
def test_repair_reports_filesystem_failure_clearly(tmp_path):
    """A backup that cannot be written must not surface as a raw OSError."""
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    db_path = readonly / "bad.db"
    db_path.write_bytes(GARBAGE * 100)
    readonly.chmod(0o500)
    try:
        with pytest.raises(DatabaseFileError):
            repair_database(db_path)
    finally:
        readonly.chmod(0o700)
    # The corrupt file is untouched: refusing to back up must refuse to repair.
    assert db_path.read_bytes() == GARBAGE * 100


def test_repair_db_flag_returns_before_any_other_mode(tmp_path, monkeypatch):
    db_path = tmp_path / "bad.db"
    db_path.write_bytes(GARBAGE * 100)

    def fail_if_reached(*_args, **_kwargs):
        raise AssertionError("--repair-db must return before any other mode runs")

    monkeypatch.setattr(sys, "argv", ["lyra", "--repair-db"])
    monkeypatch.setattr(Config, "DB_PATH", str(db_path))
    monkeypatch.setattr(Config, "validate", classmethod(fail_if_reached))
    monkeypatch.setattr(main_module, "setup_logging", lambda *_a, **_k: None)

    assert main_module.main() == 0

    with sqlite3.connect(db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 0
    assert list(tmp_path.glob("bad.db.backup-*"))
