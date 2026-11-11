"""
Explicit database maintenance: backup and repair.

Nothing here runs on its own. Startup never repairs a database, because a file
that fails a query is not proof of corruption and a locked file is not damaged
at all. The destructive part -- moving the old file aside and rebuilding an
empty schema -- happens only when an operator asks for it with
``lyra --repair-db``.
"""

import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .connection import DatabaseConnection, integrity_ok
from .errors import DatabaseFileError

#: SQLite sidecar suffixes. ``-wal`` holds commits that are not folded into the
#: main file yet and ``-shm`` is its transient shared-memory index. Both belong
#: to the file they are named after, so neither may outlive it: a stale WAL next
#: to a rebuilt database would be replayed into it.
WAL_SUFFIX = "-wal"
SHM_SUFFIX = "-shm"


@dataclass
class RepairReport:
    """What an explicit repair did, for the operator and for the log."""

    backup_path: Path
    tables: dict[str, int]
    integrity_ok: bool


def _sidecar(db_path: Path, suffix: str) -> Path:
    """Return the path SQLite uses for ``db_path``'s sidecar file."""
    return db_path.with_name(f"{db_path.name}{suffix}")


def _free_backup_path(db_path: Path) -> Path:
    """Return a backup path that does not exist yet: ``<db>.backup-<ts>-<pid>``."""
    stem = f"{db_path.name}.backup-{int(time.time())}-{os.getpid()}"
    candidate = db_path.with_name(stem)
    counter = 1
    while candidate.exists():
        candidate = db_path.with_name(f"{stem}-{counter}")
        counter += 1
    return candidate


def backup_database(db_path: Path) -> Path:
    """
    Copy the database file to a fresh backup path and return that path.

    The copy is a raw file copy on purpose: a backup must also work on a file
    SQLite refuses to read, which rules out a logical dump. A ``-wal`` sidecar is
    copied under the backup's own name, so commits that had not reached the main
    file yet are not silently dropped from the backup.

    Args:
        db_path: Path of the database file to back up.

    Returns:
        Path of the backup that was written.

    Raises:
        DatabaseFileError: If the copy could not be made. The database itself is
            never modified by a backup.
    """
    db_path = Path(db_path)
    backup_path = _free_backup_path(db_path)
    try:
        shutil.copy2(db_path, backup_path)
        wal = _sidecar(db_path, WAL_SUFFIX)
        if wal.exists():
            shutil.copy2(wal, _sidecar(backup_path, WAL_SUFFIX))
    except OSError as e:
        raise DatabaseFileError(db_path, "back up", e) from e
    return backup_path


def repair_database(db_path: Path) -> RepairReport:
    """
    Back ``db_path`` up, then rebuild an empty database with a fresh schema.

    The backup always happens first, so an unbacked file is never touched. The
    old file and its sidecars are then removed rather than renamed: a leftover
    ``-wal`` beside the rebuilt file would be replayed into it. Row counts in
    the report are the rows the rebuilt (empty) database has, which is how the
    operator sees what the old file could no longer be read for.

    Args:
        db_path: Path of the database file to repair.

    Returns:
        A :class:`RepairReport` naming the backup and summarising the new file.

    Raises:
        DatabaseFileError: If the backup or the replacement could not be done.
    """
    db_path = Path(db_path)
    backup_path = backup_database(db_path)
    try:
        for path in (
            db_path,
            _sidecar(db_path, WAL_SUFFIX),
            _sidecar(db_path, SHM_SUFFIX),
        ):
            path.unlink(missing_ok=True)
    except OSError as e:
        raise DatabaseFileError(
            db_path,
            "replace",
            f"{e} (a backup was already written to {backup_path})",
        ) from e

    DatabaseConnection(db_path).initialize()
    return RepairReport(
        backup_path=backup_path,
        tables=_table_row_counts(db_path),
        integrity_ok=integrity_ok(db_path),
    )


def _table_row_counts(db_path: Path) -> dict[str, int]:
    """Return ``{table: rows}`` for every user table in the database."""
    conn = sqlite3.connect(db_path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in names
        }
    finally:
        conn.close()
