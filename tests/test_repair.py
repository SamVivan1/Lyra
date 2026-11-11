"""Explicit repair: a lock is not corruption and nothing is renamed automatically."""

import logging
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


def test_repair_keeps_the_corrupt_bytes_in_the_backup(tmp_path, caplog):
    """The operator's evidence must survive: the backup is an exact copy."""
    db_path = tmp_path / "bad.db"
    original = GARBAGE * 100
    db_path.write_bytes(original)

    with caplog.at_level(logging.WARNING):
        report = repair_database(db_path)

    assert report.backup_path.read_bytes() == original
    assert db_path.read_bytes() != original
    # Nothing can be counted in a file that is not a database. The report then
    # says "unknown" (empty) instead of "nothing was lost" (a dict of zeros),
    # and the operator is told why the counts are missing.
    assert report.tables == {}
    assert report.backup_path.name in caplog.text


def test_repair_reports_the_rows_lost_from_the_old_database(tmp_path):
    """Repair must report what the old file held, not what the empty rebuild holds."""
    db_path = tmp_path / "live.db"
    DatabaseConnection(db_path).initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO artists (artist_name, added_at) VALUES (?, ?)",
            ("Autechre", "2026-01-01"),
        )
        conn.executemany(
            "INSERT INTO local_library (artist, title, added_at) VALUES (?, ?, ?)",
            [
                ("Autechre", "Amber", "2026-01-01"),
                ("Autechre", "Nil", "2026-01-01"),
            ],
        )
        conn.executemany(
            "INSERT INTO tracked_tracks (artist, title, added_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            [(f"Track {n}", "Autechre", "2026-01-01", "2026-01-01") for n in range(3)],
        )

    report = repair_database(db_path)

    assert report.tables == {"artists": 1, "local_library": 2, "tracked_tracks": 3}
    # The rows are really gone from the live file -- and really still in the backup.
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 0
    with sqlite3.connect(report.backup_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 2


def test_repair_counts_rows_that_were_still_only_in_the_wal(tmp_path):
    """A commit that has not reached the main file yet is still counted."""
    db_path = tmp_path / "live.db"
    DatabaseConnection(db_path).initialize()
    writer = sqlite3.connect(db_path)  # kept open, so the commit stays in the -wal
    writer.execute(
        "INSERT INTO artists (artist_name, added_at) VALUES (?, ?)",
        ("AFX", "2026-01-01"),
    )
    writer.commit()
    wal = db_path.with_name(f"{db_path.name}-wal")
    assert wal.exists(), "precondition: the commit is only in the -wal so far"
    try:
        report = repair_database(db_path)
    finally:
        writer.close()

    assert report.tables["artists"] == 1
    # Counted and backed up together: the backup really holds the row.
    with sqlite3.connect(report.backup_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0] == 1


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


def test_main_reports_a_corrupt_database_without_raising(tmp_path, monkeypatch, caplog):
    """Normal startup must hand the operator the repair command, not a traceback."""
    db_path = tmp_path / "bad.db"
    db_path.write_bytes(GARBAGE * 100)

    monkeypatch.setattr(sys, "argv", ["lyra"])
    monkeypatch.setattr(Config, "DB_PATH", str(db_path))
    monkeypatch.setattr(Config, "validate", classmethod(lambda *_a, **_k: None))
    monkeypatch.setattr(main_module, "setup_logging", lambda *_a, **_k: None)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code != 0
    assert "--repair-db" in caplog.text
    # Startup only reports: it must never repair on its own.
    assert db_path.read_bytes() == GARBAGE * 100


def test_main_reports_a_sqlite_failure_without_raising(tmp_path, monkeypatch, caplog):
    """A plain sqlite3 error at startup is reported, not raised."""
    # A directory where the database file should be: sqlite3.connect fails.
    db_path = tmp_path / "not_a_file"
    db_path.mkdir()

    monkeypatch.setattr(sys, "argv", ["lyra"])
    monkeypatch.setattr(Config, "DB_PATH", str(db_path))
    monkeypatch.setattr(Config, "validate", classmethod(lambda *_a, **_k: None))
    monkeypatch.setattr(main_module, "setup_logging", lambda *_a, **_k: None)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code != 0
    assert str(db_path) in caplog.text
