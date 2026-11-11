"""
Numbered schema migrations.

The schema version lives in SQLite's own ``PRAGMA user_version``, so the version
and the data it describes are committed together: a migration that fails
half-way leaves neither a changed schema nor a raised version behind.

Two rules make that true, and both are easy to lose:

* every statement runs through ``execute()``. ``executescript()`` commits the
  open transaction before it runs, which would break the atomicity this module
  exists for. (``executescript`` is still fine in test fixtures that *build* a
  legacy database, because nothing there needs to be undone.)
* the version is re-read after the write lock is taken. Two processes starting
  together both see version 0 beforehand; only one of them may migrate, and the
  second has to notice the work is done when it finally gets the lock.
* the backup runs *before* that lock is taken. Copying the database closes a
  descriptor on the file, and a POSIX record lock -- which is what SQLite takes
  -- is released when a process closes any descriptor for the locked file. A
  backup taken inside the transaction would drop the very lock the second rule
  depends on.

Migration v1 replaces the old table-level ``UNIQUE(artist, title,
recording_mbid)`` on ``local_library`` with the natural key
``(artist COLLATE NOCASE, title COLLATE NOCASE)``. The old constraint let the
same track in twice whenever the MusicBrainz id was NULL -- which, for a
directory scan, is always -- and no constraint could be dropped in place, so the
existing table is rebuilt. SQLite cannot express "this row is the survivor", so
the merge rules are applied in Python, with SQLite's own ``COLLATE NOCASE``
semantics (ASCII-only case folding) applied explicitly.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Union

from .errors import DatabaseFileError

logger = logging.getLogger(__name__)

#: Current schema version. Bump this and add ``_migrate_v<n>`` together.
SCHEMA_VERSION = 1

#: Report written next to the database when v1 drops rows that held their own
#: copy of the file. Audio files themselves are never touched by a migration.
DUPLICATE_REPORT_NAME = "migration-duplicates-1.txt"

#: Columns of ``local_library``, in the order every statement here expects.
_LOCAL_LIBRARY_COLUMNS = "id, artist, album, title, recording_mbid, file_path, added_at"

#: Name of the v1 unique index on the natural key.
NATURAL_KEY_INDEX = "ux_local_library_natural"

#: The v1 table. No unique constraint: NULL mbids made it useless, and a table
#: constraint cannot be dropped without rebuilding the table.
_LOCAL_LIBRARY_DDL = """
    CREATE TABLE {table} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        artist TEXT NOT NULL,
        album TEXT,
        title TEXT NOT NULL,
        recording_mbid TEXT,
        file_path TEXT,
        added_at TEXT NOT NULL
    )
"""

_ARTISTS_DDL = """
    CREATE TABLE IF NOT EXISTS artists (
        artist_name TEXT PRIMARY KEY,
        added_at TEXT NOT NULL,
        fallback_done INTEGER DEFAULT 0,
        recommended_tracks TEXT
    )
"""

_TRACKED_TRACKS_DDL = """
    CREATE TABLE IF NOT EXISTS tracked_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recording_mbid TEXT,
        artist TEXT NOT NULL,
        title TEXT NOT NULL,
        source TEXT DEFAULT 'listenbrainz',
        status TEXT DEFAULT 'PENDING',
        added_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(recording_mbid, artist, title)
    )
"""

#: The natural key, plus the lookup indexes the old schema had. ``IF NOT EXISTS``
#: throughout: a second process may already have created them.
_LOCAL_LIBRARY_INDEXES = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {NATURAL_KEY_INDEX} "
    "ON local_library(artist COLLATE NOCASE, title COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS idx_local_library_artist ON local_library(artist)",
    "CREATE INDEX IF NOT EXISTS idx_local_library_mbid ON local_library(recording_mbid)",
)

_TRACKED_TRACKS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_tracked_tracks_status ON tracked_tracks(status)",
)

#: ASCII-only lower casing, the exact folding ``COLLATE NOCASE`` performs. str.lower()
#: would fold non-ASCII too and split a group the unique index considers one row.
_ASCII_LOWER = {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}


def apply_migrations(
    conn: sqlite3.Connection, db_path: Union[str, Path]
) -> List[int]:
    """
    Bring ``conn``'s database up to :data:`SCHEMA_VERSION`.

    The version is checked twice on purpose: once without a lock (cheap, and the
    answer for every startup after the first) and once with the write lock held.
    Only the second check may lead to work, because between the two another
    process may have migrated the file -- the version it wrote is committed with
    the schema, so re-reading it is what makes "migrate once" true across
    processes.

    Args:
        conn: Open connection to the database to migrate.
        db_path: Path of that database, used for the backup and the dup report.

    Returns:
        The versions this call applied, ``[]`` when the schema was already current.
    """
    db_path = Path(db_path)
    if _user_version(conn) >= SCHEMA_VERSION:
        return []

    # The backup is taken *before* the lock, and that order is load-bearing.
    # Copying the database opens and closes a descriptor on the file, and POSIX
    # record locks -- the locks SQLite uses -- are released when a process
    # closes *any* descriptor for the locked file. A copy taken inside the
    # transaction would therefore silently drop the write lock that makes
    # "migrate once" true: two starters would migrate on top of each other and
    # one of them would fail at COMMIT with ``disk I/O error``.
    backup_path = _backup_before_migrating(conn, db_path)

    applied: List[int] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = _user_version(conn)
        if version >= SCHEMA_VERSION:
            # Another process migrated while this one waited for the lock. The
            # copy just taken is not the pre-migration state a backup is for --
            # it may even be a snapshot from the middle of that process's
            # transaction -- so it goes again rather than being left behind.
            _discard_backup(backup_path)
            conn.commit()
            return []
        if version < 1:
            _migrate_v1(conn, db_path)
            applied.append(1)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return applied


def _migrate_v1(conn: sqlite3.Connection, db_path: Path) -> None:
    """
    Create the v1 schema, merging an existing ``local_library`` into it.

    A fresh database has nothing to rebuild, so the table is simply created with
    the natural key; an existing one is deduped and rebuilt. Both end at
    ``user_version = 1``.

    The duplicate report is written last: a report that survived a rolled-back
    migration would describe rows that are still there.

    Args:
        conn: Connection with an open write transaction.
        db_path: Path of the database, for the duplicate report.
    """
    conn.execute(_ARTISTS_DDL)
    conn.execute(_TRACKED_TRACKS_DDL)

    dropped: List[str] = []
    if _table_exists(conn, "local_library"):
        if _local_library_is_v1(conn):
            # Already rebuilt: a second process raced this one past the version
            # check, or a previous run committed the table and died before the
            # version write. Rebuilding again would only renumber and re-report.
            logger.debug("local_library is already in its v1 shape")
        else:
            kept, dropped = _merge_local_library(conn)
            _rebuild_local_library(conn, kept)
    else:
        conn.execute(_LOCAL_LIBRARY_DDL.format(table="local_library"))

    for statement in _LOCAL_LIBRARY_INDEXES + _TRACKED_TRACKS_INDEXES:
        conn.execute(statement)

    _set_user_version(conn, SCHEMA_VERSION)
    _write_duplicate_report(db_path, dropped)


def _merge_local_library(
    conn: sqlite3.Connection,
) -> tuple[List[tuple], List[str]]:
    """
    Merge rows that share the natural key, keeping the lowest id.

    Merge rules, and only these: ``file_path`` and ``recording_mbid`` come from
    the winning row; a NULL there is filled from the next row in id order that
    has a value. Every losing row that holds a *different* non-NULL
    ``file_path`` is reported, because that path is knowledge the merge
    destroys -- the audio file is left on disk, but nothing in the database
    points at it any more.

    The merged rows are computed here rather than written back into the old
    table: its table-level unique constraint would reject the very update that
    records the merged values, and the table is about to be replaced anyway.

    Args:
        conn: Connection to the database whose ``local_library`` is merged.

    Returns:
        The surviving rows in id order, and one report line per dropped path.
    """
    rows = conn.execute(
        f"SELECT {_LOCAL_LIBRARY_COLUMNS} FROM local_library ORDER BY id"
    ).fetchall()

    groups: dict[tuple[str, str], list[tuple]] = {}
    for row in rows:
        row = tuple(row)
        groups.setdefault((_fold(row[1]), _fold(row[3])), []).append(row)

    kept: List[tuple] = []
    dropped: List[str] = []
    for members in groups.values():
        winner = members[0]
        if len(members) > 1:
            recording_mbid = winner[4]
            file_path = winner[5]
            for row in members[1:]:
                if recording_mbid is None:
                    recording_mbid = row[4]
                if file_path is None:
                    file_path = row[5]
            winner = (winner[0], winner[1], winner[2], winner[3],
                      recording_mbid, file_path, winner[6])
            for row in members[1:]:
                if row[5] is not None and row[5] != file_path:
                    dropped.append(f"{row[0]}\t{row[1]}\t{row[3]}\t{row[5]}")
        kept.append(winner)
    kept.sort(key=lambda row: row[0])
    if len(kept) != len(rows):
        logger.warning(
            f"Merged {len(rows) - len(kept)} duplicate local_library row(s) into "
            f"{len(kept)} using the natural key artist + title"
        )
    return kept, dropped


def _rebuild_local_library(conn: sqlite3.Connection, rows: Iterable[tuple]) -> None:
    """
    Recreate ``local_library`` in the v1 shape from ``rows``, keeping each id.

    SQLite cannot drop the old table-level ``UNIQUE(artist, title,
    recording_mbid)``, and leaving it in place would keep rejecting (or, worse,
    keep allowing) exactly the rows the natural key is meant to judge.

    Args:
        conn: Connection with an open write transaction.
        rows: The rows to keep, in the v1 column order.
    """
    conn.execute("DROP TABLE IF EXISTS local_library_v1")
    conn.execute(_LOCAL_LIBRARY_DDL.format(table="local_library_v1"))
    conn.executemany(
        f"INSERT INTO local_library_v1 ({_LOCAL_LIBRARY_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [tuple(row) for row in rows],
    )
    conn.execute("DROP TABLE local_library")
    conn.execute("ALTER TABLE local_library_v1 RENAME TO local_library")


def _backup_before_migrating(
    conn: sqlite3.Connection, db_path: Path
) -> Optional[Path]:
    """
    Back the database up when a migration is about to rewrite existing data.

    Only an existing ``local_library`` holds anything a migration can lose, so a
    database that is being created for the first time is not backed up: a copy
    of an empty file protects nothing and would leave nightly clutter in the
    data directory. Every startup after the first is a no-op anyway, because
    ``apply_migrations`` returns before this is reached.

    Called before the migration's write transaction is opened, never inside it:
    the copy opens and closes a descriptor on the database file, which would
    release the POSIX lock SQLite holds through it.

    Args:
        conn: Connection to the database, read only for the table check.
        db_path: Path of the database file to copy.

    Returns:
        The backup path, or ``None`` when no backup was needed.
    """
    if not _table_exists(conn, "local_library") or not db_path.exists():
        return None
    # Imported here, not at module level: maintenance imports this module's
    # sibling connection.py, which imports this module.
    from .maintenance import backup_database

    backup_path = backup_database(db_path)
    logger.info(
        f"Backed up {db_path} to {backup_path} before migrating the schema"
    )
    return backup_path


def _discard_backup(backup_path: Optional[Path]) -> None:
    """
    Remove a backup that turned out not to be needed, and its WAL sidecar.

    Called when another process migrated while this one was waiting for the
    lock. That copy was taken before the lock, so it may predate or straddle the
    other process's transaction; it is not a state anyone should restore from,
    and keeping it would present it as the pre-migration file. Removing it keeps
    "one migration, one backup" true.

    Args:
        backup_path: Backup to remove, or ``None`` when none was taken.
    """
    if backup_path is None:
        return
    # Imported here for the same reason as in ``_backup_before_migrating``, and
    # to reuse the suffix that ``backup_database`` names the sidecar copy with.
    from .maintenance import WAL_SUFFIX

    try:
        backup_path.unlink(missing_ok=True)
        backup_path.with_name(f"{backup_path.name}{WAL_SUFFIX}").unlink(missing_ok=True)
    except OSError as e:
        logger.warning(
            f"Could not remove the unnecessary backup {backup_path}: {e}"
        )
        return
    logger.info(
        f"Another process migrated the database first; removed the backup "
        f"{backup_path} that this process no longer needs"
    )


def _write_duplicate_report(db_path: Path, lines: Iterable[str]) -> Optional[Path]:
    """
    Write the dropped-file-path report next to the database.

    Written only when a path was really dropped, so the absence of the file
    means "nothing was lost" instead of "the migration did not care".

    Args:
        db_path: Path of the database the report belongs to.
        lines: Report lines, one per dropped path.

    Returns:
        The report path, or ``None`` when there was nothing to report.
    """
    lines = list(lines)
    if not lines:
        return None
    report_path = Path(db_path).parent / DUPLICATE_REPORT_NAME
    try:
        report_path.write_text(
            "".join(f"{line}\n" for line in lines), encoding="utf-8"
        )
    except OSError as e:
        raise DatabaseFileError(db_path, "write the duplicate report for", e) from e
    logger.warning(
        f"Dropped {len(lines)} duplicate file path(s) while migrating {db_path}; "
        f"they are listed in {report_path}. The audio files themselves are untouched."
    )
    return report_path


def _fold(value: str) -> str:
    """Fold ``value`` the way ``COLLATE NOCASE`` does: ASCII letters only."""
    return value.translate(_ASCII_LOWER)


def _user_version(conn: sqlite3.Connection) -> int:
    """Return the schema version stored in the database header."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Store ``version`` in the database header, inside the running transaction."""
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Return True when a table called ``name`` exists in the database."""
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def _local_library_is_v1(conn: sqlite3.Connection) -> bool:
    """
    Return True when ``local_library`` already has the v1 shape.

    Two things are checked, because either alone can lie: the natural-key index
    exists, and the table carries no table-level ``UNIQUE`` constraint (which
    SQLite implements as an ``sqlite_autoindex_`` entry). An index alone could
    have been added to a legacy table by hand; an autoindex alone is what an
    older release wrote.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
        (NATURAL_KEY_INDEX,),
    ).fetchone():
        return False
    return not any(
        str(row[1]).startswith("sqlite_autoindex_")
        for row in conn.execute("PRAGMA index_list(local_library)").fetchall()
    )
