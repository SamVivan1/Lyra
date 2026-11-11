"""
Explicit database maintenance: backup and repair.

Nothing here runs on its own. Startup never repairs a database, because a file
that fails a query is not proof of corruption and a locked file is not damaged
at all. The destructive part -- moving the old file aside and rebuilding an
empty schema -- happens only when an operator asks for it with
``lyra --repair-db``.
"""

import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .connection import DatabaseConnection, integrity_ok
from .errors import DatabaseFileError

logger = logging.getLogger(__name__)

#: SQLite sidecar suffixes. ``-wal`` holds commits that are not folded into the
#: main file yet and ``-shm`` is its transient shared-memory index. Both belong
#: to the file they are named after, so neither may outlive it: a stale WAL next
#: to a rebuilt database would be replayed into it.
WAL_SUFFIX = "-wal"
SHM_SUFFIX = "-shm"


@dataclass
class RepairReport:
    """
    What an explicit repair did, for the operator and for the log.

    Attributes:
        backup_path: Where the pre-repair file was saved.
        tables: ``{table: rows}`` **of the old database**, counted in the backup,
            so the operator can see what the rebuild cost. Empty when the old
            file was too damaged to count.
        integrity_ok: Whether the *rebuilt* database passes its integrity check.
    """

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


def _free_rebuild_path(db_path: Path) -> Path:
    """Return an unused path to build the replacement database at.

    The replacement is built beside the live file, under a name of its own, and
    renamed over it only after it passes its integrity check. Keeping it in the
    same directory matters: ``os.replace`` is atomic only within a filesystem,
    and a rebuild that failed must never be able to touch the original.
    """
    stem = f"{db_path.name}.rebuild-{int(time.time())}-{os.getpid()}"
    candidate = db_path.with_name(stem)
    counter = 1
    while candidate.exists():
        candidate = db_path.with_name(f"{stem}-{counter}")
        counter += 1
    return candidate


def _discard_rebuild(rebuild_path: Path) -> None:
    """Remove a replacement that is not going to be renamed into place.

    Its sidecars go with it: a leftover ``-wal`` from a failed rebuild would be
    replayed into the next file that carried that name.
    """
    for path in (
        rebuild_path,
        _sidecar(rebuild_path, WAL_SUFFIX),
        _sidecar(rebuild_path, SHM_SUFFIX),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Could not remove the abandoned rebuild {path}: {e}")


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
    Back ``db_path`` up, then replace it with a fresh, empty database.

    The backup always happens first, so an unbacked file is never touched. The
    replacement is then built under a name of its own beside the live file and
    renamed into place only after it passes ``PRAGMA integrity_check``. That
    order is the safety property: a rebuild that fails leaves the operator's
    database exactly where it was, and every failure message names the backup,
    because that copy is the only one that still holds the old rows.

    The old sidecars are removed before the rename, and the replacement's own
    are checkpointed away before it moves: a ``-wal`` belongs to the file it is
    named after, so one left beside the new file would be replayed into it.

    The row counts in the report are the rows of the **old** file, counted in the
    backup taken a moment earlier. That is what the operator needs: the rebuilt
    database is empty by construction, so counting it would always say "nothing
    was lost" -- exactly when everything was.

    Args:
        db_path: Path of the database file to repair.

    Returns:
        A :class:`RepairReport` naming the backup and saying what it held.

    Raises:
        DatabaseFileError: If the backup, the rebuild or the replacement could
            not be done. The original file survives every one of those.
    """
    db_path = Path(db_path)
    backup_path = backup_database(db_path)
    # Counted before the old file is replaced, and from the backup, so the
    # numbers describe the file as it was backed up even if a later step fails.
    lost_rows = _lost_row_counts(backup_path)

    rebuild_path = _free_rebuild_path(db_path)
    try:
        DatabaseConnection(rebuild_path).initialize()
        if not integrity_ok(rebuild_path):
            raise DatabaseFileError(
                db_path,
                "rebuild",
                f"the replacement at {rebuild_path} failed PRAGMA integrity_check "
                f"(a backup of the old database is at {backup_path})",
            )
        _seal_rebuild(rebuild_path)
        _install_rebuild(db_path, rebuild_path, backup_path)
    except DatabaseFileError:
        _discard_rebuild(rebuild_path)
        raise
    except (sqlite3.Error, OSError) as e:
        # A rebuild failure must reach the operator as one actionable line that
        # names the backup -- and it must never take the live file with it, which
        # is why the rebuild happened at a path of its own.
        _discard_rebuild(rebuild_path)
        raise DatabaseFileError(
            db_path,
            "rebuild",
            f"{e} (a backup of the old database is at {backup_path})",
        ) from e
    return RepairReport(
        backup_path=backup_path,
        tables=lost_rows,
        integrity_ok=integrity_ok(db_path),
    )


def _seal_rebuild(rebuild_path: Path) -> None:
    """
    Fold the replacement's WAL into it and drop its sidecars.

    ``initialize()`` leaves a WAL-mode database, and a WAL may still hold commits
    that are not in the main file. Renaming only the main file would leave those
    commits behind under the old name, so they are checkpointed in first; the
    emptied sidecars are then removed and the file stands alone.
    """
    conn = sqlite3.connect(rebuild_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    for suffix in (WAL_SUFFIX, SHM_SUFFIX):
        _sidecar(rebuild_path, suffix).unlink(missing_ok=True)


def _install_rebuild(db_path: Path, rebuild_path: Path, backup_path: Path) -> None:
    """
    Put a verified replacement where the live database was.

    The old file is not removed first: ``os.replace`` swaps the two atomically,
    so the live database exists until the replacement is there. Only the old
    sidecars are removed beforehand, because they belong to the file being
    replaced and would be replayed into its successor.
    """
    try:
        for suffix in (WAL_SUFFIX, SHM_SUFFIX):
            _sidecar(db_path, suffix).unlink(missing_ok=True)
        os.replace(rebuild_path, db_path)
    except OSError as e:
        raise DatabaseFileError(
            db_path,
            "replace",
            f"{e} (a backup of the old database is at {backup_path})",
        ) from e


def _lost_row_counts(backup_path: Path) -> dict[str, int]:
    """
    Count the rows the repair is about to lose, read from the pre-repair backup.

    Best effort: a file too damaged to read must not turn a repair into a second
    failure, so an unreadable backup yields an empty mapping and a warning. An
    empty mapping is honest ("how much was lost is unknown"); a mapping of zeros
    would read as "nothing was lost", which is the confusion this report exists
    to remove. Reading the backup may fold its own ``-wal`` copy into it; the
    live database is never involved.

    Args:
        backup_path: Backup written just before the rebuild.

    Returns:
        ``{table: rows}`` of the old database, or ``{}`` if it cannot be read.
    """
    try:
        return _table_row_counts(backup_path)
    except (sqlite3.Error, OSError) as e:
        logger.warning(
            f"Could not count the rows in the pre-repair backup at {backup_path}: {e}. "
            "The report carries no row counts; how much the old database held is unknown."
        )
        return {}


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
