"""
Numbered schema migrations.

The schema version lives in SQLite's own ``PRAGMA user_version``, so the version
and the data it describes are committed together: a migration that fails
half-way leaves neither a changed schema nor a raised version behind.

Four rules make that true, and each is easy to lose:

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
* the duplicate report is written *after* the commit, not before it. Rolling
  back cannot unlink a file, so a report written inside the transaction would
  survive the rollback and describe paths that are still in the database. Since
  the committed schema is the only thing that makes those paths really dropped,
  the report follows it.

Migration v1 replaces the old table-level ``UNIQUE(artist, title,
recording_mbid)`` on ``local_library`` with the natural key
``(artist COLLATE NOCASE, title COLLATE NOCASE)``. The old constraint let the
same track in twice whenever the MusicBrainz id was NULL -- which, for a
directory scan, is always -- and no constraint could be dropped in place, so the
existing table is rebuilt. SQLite cannot express "this row is the survivor", so
the merge rules are applied in Python, with SQLite's own ``COLLATE NOCASE``
semantics (ASCII-only case folding) applied explicitly.

Migration v2 gives ``tracked_tracks`` the columns the retry state machine needs
(``attempts``, ``last_attempt_at``, ``next_retry_at``, ``last_error``,
``lidarr_checked_at``), renames the legacy ``PENDING`` status to ``NEW``, merges
the rows that share the natural key, and puts the unique index on that key. It
also rebuilds ``artists`` without ``fallback_done``: no code in this branch ever
set it, and ``ALTER TABLE ... DROP COLUMN`` would need a newer SQLite than the
oldest interpreter in the CI matrix ships.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Union

from .errors import DatabaseFileError

logger = logging.getLogger(__name__)

#: Current schema version. Bump this and add ``_migrate_v<n>`` together.
SCHEMA_VERSION = 2

#: Report written next to the database for the rows a migration drops. One file
#: per version, so v1's list of dropped audio file paths is not overwritten by
#: v2's list of dropped duplicate tracks: an operator acting on a report must
#: never be reading another migration's findings. Audio files themselves are
#: never touched by a migration.
DUPLICATE_REPORT_TEMPLATE = "migration-duplicates-{version}.txt"

#: Columns of ``local_library``, in the order every statement here expects.
_LOCAL_LIBRARY_COLUMNS = "id, artist, album, title, recording_mbid, file_path, added_at"

#: Name of the v1 unique index on the natural key.
NATURAL_KEY_INDEX = "ux_local_library_natural"

#: Name of the v2 unique index on the natural key of ``tracked_tracks``.
TRACKED_NATURAL_KEY_INDEX = "ux_tracked_natural"

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

#: The v1 table, kept exactly as v1 released it: v2 adds its columns to this
#: shape instead of editing it. The ``status`` default is the legacy queue head
#: and is only reachable by an INSERT that names no status -- every writer in
#: this branch names ``NEW`` explicitly.
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

#: The v2 state-machine columns of ``tracked_tracks``, as ``(name, definition)``.
#: Every one is nullable or defaulted: a NOT NULL column cannot be added without
#: a default, and a legacy row genuinely has no attempt history to carry. Those
#: defaults *are* the reset a migrated row needs -- ``attempts`` 0 because no
#: attempt was ever counted, ``next_retry_at`` NULL because no retry has been
#: scheduled yet.
_TRACKED_TRACK_STATE_COLUMNS = (
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("last_attempt_at", "TEXT"),
    ("next_retry_at", "TEXT"),
    ("last_error", "TEXT"),
    ("lidarr_checked_at", "TEXT"),
)

#: The v2 indexes: the natural key (which makes the insert idempotent) and the
#: queue lookup the retry worker reads with ``ORDER BY next_retry_at``.
_TRACKED_TRACK_V2_INDEXES = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {TRACKED_NATURAL_KEY_INDEX} "
    "ON tracked_tracks(artist COLLATE NOCASE, title COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_tracked_queue "
    "ON tracked_tracks(status, next_retry_at)",
)

#: The v2 table. No ``fallback_done``: this branch never reads or writes it, and
#: dropping it by rebuilding is the only way that works on every SQLite version
#: this project supports.
_ARTISTS_V2_DDL = """
    CREATE TABLE {table} (
        artist_name TEXT PRIMARY KEY,
        added_at TEXT NOT NULL,
        recommended_tracks TEXT
    )
"""

#: Merge precedence for duplicate ``tracked_tracks`` rows, strongest first. A
#: track that completed is not reopened by a duplicate that is still queued, so
#: the status decides before the id does.
_STATUS_PRECEDENCE = (
    "COMPLETED",
    "RUNNING",
    "QUEUED_FALLBACK",
    "WAITING_LIDARR",
    "NEW",
    "FAILED",
    "DEAD",
)

_STATUS_RANK = {status: rank for rank, status in enumerate(_STATUS_PRECEDENCE)}

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

    A lock that cannot be taken is fatal here, deliberately, and is the one
    failure this function cleans up after. ``BEGIN IMMEDIATE`` waits the
    connection's ``busy_timeout`` (5 s) before answering ``database is locked``,
    which is longer than a competing starter's migration takes, so it is not a
    race worth retrying; and carrying on without the lock would report a schema
    that is still at the old version as migrated, which is worse than refusing to
    start. What must not survive it is the backup, which is taken before the lock
    and therefore exists for a migration that never ran.

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
    dropped: dict[int, List[str]] = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
    except BaseException as e:
        # No transaction is open, so there is nothing to roll back -- but the
        # copy above protects a migration that is not going to happen, and
        # leaving it would make ``db.backup-*`` mean less than "a migration
        # ran". The error keeps its own type: ``_migrate()`` classifies a lock
        # as a concurrency condition, never as corruption.
        _discard_backup(backup_path, f"the write lock could not be taken ({e})")
        logger.warning(
            f"Could not migrate {db_path}: {e}. Startup is failing rather than "
            "running against a schema that is not current."
        )
        raise
    try:
        version = _user_version(conn)
        if version >= SCHEMA_VERSION:
            # Another process migrated while this one waited for the lock. The
            # copy just taken is not the pre-migration state a backup is for --
            # it may even be a snapshot from the middle of that process's
            # transaction -- so it goes again rather than being left behind.
            _discard_backup(backup_path, "another process migrated the database first")
            conn.commit()
            return []
        if version < 1:
            dropped[1] = _migrate_v1(conn, db_path)
            applied.append(1)
        if version < 2:
            dropped[2] = _migrate_v2(conn, db_path)
            applied.append(2)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    # After the commit, never before it: a rollback cannot delete a file, so a
    # report written inside the transaction would outlive the rollback and name
    # rows and paths the database still holds. A report of a migration that did
    # commit cannot describe anything that is still there. Each version writes
    # its own file, so a v2 merge never overwrites v1's dropped paths -- an
    # operator acts on the file a specific migration produced.
    for applied_version in applied:
        _write_duplicate_report(
            db_path, applied_version, dropped.get(applied_version, [])
        )
    return applied


def _migrate_v1(conn: sqlite3.Connection, db_path: Path) -> List[str]:
    """
    Create the v1 schema, merging an existing ``local_library`` into it.

    A fresh database has nothing to rebuild, so the table is simply created with
    the natural key; an existing one is deduped and rebuilt. Both end at
    ``user_version = 1``.

    The duplicate report is *not* written here. Rolling back cannot remove a file
    the way it removes rows, so a report written inside the migration's
    transaction could survive a failed ``COMMIT`` and describe paths the database
    still references -- and the one thing an operator does with that file is
    delete the paths it lists. The dropped paths are returned instead, and
    :func:`apply_migrations` writes them once the commit has succeeded.

    Args:
        conn: Connection with an open write transaction.
        db_path: Path of the database, kept for the signature every caller and
            the rollback test rely on.

    Returns:
        One report line per file path the merge dropped.
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

    # The version this step reaches, not :data:`SCHEMA_VERSION`: a migration knows
    # the shape it builds, and v2 must be able to add its columns on top of it.
    _set_user_version(conn, 1)
    return dropped


def _migrate_v2(conn: sqlite3.Connection, db_path: Path) -> List[str]:
    """
    Give ``tracked_tracks`` the state-machine columns, then merge its duplicates.

    Order matters in both directions: the legacy status is renamed *before* the
    merge, because the merge ranks rows by status and ``PENDING`` is the old name
    of the queue head; and the unique index is created *after* it, because the
    merge is what makes the index creatable.

    The columns are added with ``ALTER TABLE ... ADD COLUMN`` rather than by
    rebuilding the table: the table-level ``UNIQUE(recording_mbid, artist, title)``
    that v1 left behind keeps working, and the natural-key index makes the insert
    idempotent on its own. The column defaults are the reset a migrated row
    needs: no attempt has ever been counted ("attempts = 0") and no retry has
    been scheduled ("next_retry_at = NULL"), which is true by construction
    because neither column existed before this migration.

    Args:
        conn: Connection with an open write transaction.
        db_path: Path of the database, kept for the signature every caller and
            the rollback test rely on.

    Returns:
        One report line per duplicate row the merge dropped.
    """
    conn.execute("UPDATE tracked_tracks SET status = 'NEW' WHERE status = 'PENDING'")

    for name, definition in _TRACKED_TRACK_STATE_COLUMNS:
        conn.execute(f"ALTER TABLE tracked_tracks ADD COLUMN {name} {definition}")

    dropped = _merge_tracked_tracks(conn)

    for statement in _TRACKED_TRACK_V2_INDEXES:
        conn.execute(statement)

    _rebuild_artists(conn)

    _set_user_version(conn, 2)
    return dropped


def _merge_tracked_tracks(conn: sqlite3.Connection) -> List[str]:
    """
    Merge ``tracked_tracks`` rows that share the natural key, keeping the best one.

    Winner: the highest-precedence status, then the lowest id. The status decides
    first on purpose -- a track that already completed must not be reopened by a
    duplicate that is still queued. Two merge rules, and only these:
    ``attempts`` is the maximum over the merged rows (retry history is the one
    column where a losing row holds the better value, and forgetting it would give
    a track extra tries it already spent), and ``last_error`` follows the winner,
    so the error on record describes the state the track is really in.

    Rows are merged in Python for the same reason v1 merges ``local_library`` in
    Python: SQLite cannot express "this row is the survivor", and the grouping
    must use the ASCII-only folding ``COLLATE NOCASE`` performs.

    Args:
        conn: Connection with an open write transaction.

    Returns:
        One report line (``id, artist, title``) per dropped row, in id order.
    """
    rows = conn.execute(
        "SELECT id, artist, title, status, attempts, last_error FROM tracked_tracks "
        "ORDER BY id"
    ).fetchall()

    groups: dict[tuple[str, str], list[tuple]] = {}
    for row in rows:
        row = tuple(row)
        groups.setdefault((_fold(row[1]), _fold(row[2])), []).append(row)

    dropped: List[str] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        winner = min(members, key=lambda row: (_status_rank(row[3]), row[0]))
        attempts = max(int(row[4] or 0) for row in members)
        conn.execute(
            "UPDATE tracked_tracks SET attempts = ? WHERE id = ?",
            (attempts, winner[0]),
        )
        losers = [row for row in members if row[0] != winner[0]]
        for row in losers:
            dropped.append(f"{row[0]}\t{row[1]}\t{row[2]}")
        conn.executemany(
            "DELETE FROM tracked_tracks WHERE id = ?", [(row[0],) for row in losers]
        )
    if dropped:
        logger.warning(
            f"Merged {len(dropped)} duplicate tracked_tracks row(s) using the "
            "natural key artist + title"
        )
    return dropped


def _rebuild_artists(conn: sqlite3.Connection) -> None:
    """
    Recreate ``artists`` without ``fallback_done``, keeping every row.

    ``ALTER TABLE ... DROP COLUMN`` needs SQLite 3.35 or newer and the CI matrix
    includes interpreters that link an older system SQLite, so the column is
    dropped the way v1 drops a table constraint: build the new table, copy the
    rows, drop the old one, rename. ``artist_name`` is the primary key, so the
    copy keeps the table's identity as well as its rows.

    Args:
        conn: Connection with an open write transaction.
    """
    if not _table_exists(conn, "artists"):
        conn.execute(_ARTISTS_V2_DDL.format(table="artists"))
        return
    if "fallback_done" not in _column_names(conn, "artists"):
        logger.debug("artists is already in its v2 shape")
        return
    conn.execute("DROP TABLE IF EXISTS artists_v2")
    conn.execute(_ARTISTS_V2_DDL.format(table="artists_v2"))
    conn.execute(
        "INSERT INTO artists_v2 (artist_name, added_at, recommended_tracks) "
        "SELECT artist_name, added_at, recommended_tracks FROM artists"
    )
    conn.execute("DROP TABLE artists")
    conn.execute("ALTER TABLE artists_v2 RENAME TO artists")


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


def _discard_backup(backup_path: Optional[Path], reason: str) -> None:
    """
    Remove a backup that turned out not to be needed, and its WAL sidecar.

    Called on the two paths where a copy exists for a migration that is not
    going to happen: another process migrated while this one waited for the
    lock, and the lock could not be taken at all. In both cases the copy was
    taken before the lock, so it may predate or straddle another process's
    transaction; it is not a state anyone should restore from, and keeping it
    would present it as the pre-migration file. Removing it keeps "a backup
    exists exactly when a migration ran" true.

    Nothing here touches a database descriptor, so it cannot release a lock.

    Args:
        backup_path: Backup to remove, or ``None`` when none was taken.
        reason: Why the copy is not needed, for the log line.
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
    logger.info(f"Removed the backup {backup_path}: {reason}")


def _write_duplicate_report(
    db_path: Path, version: int, lines: Iterable[str]
) -> Optional[Path]:
    """
    Write the report for the rows ``version`` dropped, next to the database.

    Written only when something was really dropped, so the absence of a file
    means "nothing was lost" instead of "the migration did not care". Each
    version writes its own file (``migration-duplicates-<version>.txt``): the two
    migrations drop different things -- v1 file paths, v2 duplicate tracks -- and
    one report must never overwrite the other's findings.

    Called *after* the migration's commit, never inside its transaction: a
    rollback undoes rows but not files, so a report written first would survive
    a failed commit and name paths the database still references.

    Args:
        db_path: Path of the database the report belongs to.
        version: Schema version of the migration that dropped the rows.
        lines: Report lines, one per dropped row or path.

    Returns:
        The report path, or ``None`` when there was nothing to report.
    """
    lines = list(lines)
    if not lines:
        return None
    report_path = _duplicate_report_path(db_path, version)
    try:
        report_path.write_text(
            "".join(f"{line}\n" for line in lines), encoding="utf-8"
        )
    except OSError as e:
        # The report is written after the migration committed, so this failure
        # cannot undo the merge and a later startup will not rewrite the file.
        # The paths go into the log for exactly that reason: they are the only
        # record of what the merge dropped.
        logger.error(
            f"Could not write the duplicate report for {db_path}: {e}. "
            f"The rows it describes are already merged; the dropped rows were: "
            f"{'; '.join(lines)}"
        )
        raise DatabaseFileError(db_path, "write the duplicate report for", e) from e
    logger.warning(
        f"Dropped {len(lines)} row(s) while migrating {db_path}; "
        f"they are listed in {report_path}."
    )
    return report_path


def _duplicate_report_path(db_path: Union[str, Path], version: int) -> Path:
    """Return the report path for ``version``, in ``db_path``'s directory."""
    return Path(db_path).parent / DUPLICATE_REPORT_TEMPLATE.format(version=int(version))


def _fold(value: str) -> str:
    """Fold ``value`` the way ``COLLATE NOCASE`` does: ASCII letters only."""
    return value.translate(_ASCII_LOWER)


def _status_rank(status: Optional[str]) -> int:
    """
    Rank a ``tracked_tracks`` status for the merge: lower wins.

    A status this schema does not know loses to every one it does, and is then
    decided by id alone -- it is not treated as the strongest, because an
    unrecognised value says nothing about how far the track got.
    """
    return _STATUS_RANK.get(status or "", len(_STATUS_PRECEDENCE))


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


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names of ``table``."""
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


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
