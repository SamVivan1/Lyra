"""Migrasi bernomor, kunci natural ``local_library``, dan laporan duplikat.

The tests below pin six separate promises:

* an existing database is migrated in place, duplicates merged by the natural
  key ``(artist COLLATE NOCASE, title COLLATE NOCASE)``, with the paths that
  were dropped reported next to the file;
* a fresh database is created directly at the current version, with the same
  shape and without a pointless backup or an empty report;
* a migration either happens or does not: a failure, including one after part
  of the work is done, leaves the database exactly as it was;
* the version check is the one that decides, so a second process (or the next
  startup) skips the work instead of repeating it;
* a backup exists exactly when a migration ran, including on the path where the
  write lock could not be taken at all;
* the duplicate report is written only after the commit that makes its paths
  really dropped, so it can never describe rows the database still holds.
"""

import logging
import sqlite3
import subprocess
import sys

import pytest

from lyra.database import migrations
from lyra.database.connection import DatabaseConnection

LEGACY = """
CREATE TABLE local_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT, artist TEXT NOT NULL, album TEXT,
    title TEXT NOT NULL, recording_mbid TEXT, file_path TEXT, added_at TEXT NOT NULL,
    UNIQUE(artist, title, recording_mbid));
"""

NOW = "2026-01-01T00:00:00+00:00"


def _legacy_db(tmp_path):
    db = tmp_path / "legacy.db"
    c = sqlite3.connect(db)
    c.executescript(LEGACY)   # executescript hanya untuk MENYIAPKAN data uji, bukan migrasi
    for i in range(3):
        c.execute("INSERT INTO local_library (artist,album,title,recording_mbid,file_path,added_at)"
                  " VALUES (?,?,?,NULL,?,?)",
                  ("Cup of Joe", "Multo", "Multo", "/music/a.mp3" if i == 0 else None, NOW))
    c.execute("INSERT INTO local_library (artist,album,title,recording_mbid,file_path,added_at)"
              " VALUES ('cup of joe','Multo','multo',NULL,'/music/b.mp3',?)", (NOW,))
    c.commit(); c.close()
    return db


def _legacy_db_with(db, rows):
    """Build a legacy database holding ``(artist, title, recording_mbid, file_path)``."""
    c = sqlite3.connect(db)
    c.executescript(LEGACY)
    c.executemany(
        "INSERT INTO local_library (artist,album,title,recording_mbid,file_path,added_at)"
        " VALUES (?,NULL,?,?,?,?)",
        [(artist, title, mbid, path, NOW) for artist, title, mbid, path in rows],
    )
    c.commit()
    c.close()
    return db


def _rows(db):
    c = sqlite3.connect(db)
    try:
        return c.execute(
            "SELECT id, artist, title, recording_mbid, file_path FROM local_library ORDER BY id"
        ).fetchall()
    finally:
        c.close()


def _user_version(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def _table_sql(db, name):
    c = sqlite3.connect(db)
    try:
        row = c.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None
    finally:
        c.close()


def test_dedupe_keeps_lowest_id_and_records_losing_paths(tmp_path):
    db = _legacy_db(tmp_path)
    conn = DatabaseConnection(db)
    conn.initialize()
    with conn.get_connection() as c:
        rows = c.execute("SELECT artist,title,file_path FROM local_library").fetchall()
    assert len(rows) == 1
    assert rows[0][2] == "/music/a.mp3"
    report = tmp_path / "migration-duplicates-1.txt"
    assert report.exists()
    assert "/music/b.mp3" in report.read_text()


def test_unique_index_blocks_duplicate_insert(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as c:
        c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('A','B','x')")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('a','b','y')")


def test_migration_is_idempotent(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    from lyra.database.migrations import apply_migrations
    with conn.get_connection() as c:
        assert apply_migrations(c, tmp_path / "t.db") == []


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    """Kalau satu langkah migrasi gagal, tidak ada yang setengah jadi."""
    db = _legacy_db(tmp_path)
    from lyra.database import migrations

    def boom(conn, db_dir):
        raise RuntimeError("disk full")

    monkeypatch.setattr(migrations, "_migrate_v1", boom)
    conn = DatabaseConnection(db)
    with pytest.raises(RuntimeError):
        conn.initialize()
    c = sqlite3.connect(db)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 4
    c.close()


def test_two_processes_migrate_only_once(tmp_path):
    """Dua proses start bersamaan: satu memigrasi, yang lain menunggu lalu melewati."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    db = _legacy_db(tmp_path)
    code = (
        f"import sys; sys.path.insert(0, {str(repo / 'src')!r});"
        f"from lyra.database.connection import DatabaseConnection;"
        f"DatabaseConnection({str(db)!r}).initialize()"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code], cwd=str(repo),
                         stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    errs = [p.communicate()[1] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], errs
    c = sqlite3.connect(db)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 1
    c.close()


def test_scan_twice_does_not_add_rows(tmp_path):
    """Inti goal 4: memindai library dua kali tidak menambah baris."""
    from lyra.database.library_scanner import LibraryScanner

    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    album = tmp_path / "Music" / "Artist" / "Album"
    album.mkdir(parents=True)
    (album / "Song.mp3").write_bytes(b"\x00")
    for _ in range(2):
        LibraryScanner(str(tmp_path / "Music"), conn).scan_library(force=False)
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 1


def test_non_ascii_case_is_not_folded(tmp_path):
    """Batas yang diketahui: COLLATE NOCASE hanya melipat ASCII.

    The two names differ in an ASCII letter, which NOCASE folds, and in an
    accented one, which it does not. The natural key is therefore not
    Unicode-aware: ``Beyoncé`` and ``BEYONCÉ`` are two rows, exactly like
    ``Beyoncé`` and ``beyoncé`` would be one.
    """
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as c:
        c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('Beyoncé','X','x')")
        c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('BEYONCÉ','X','y')")
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 2


# --------------------------------------------------------------------------
# Merge rules: what exactly survives a dedupe, and what is reported.
# --------------------------------------------------------------------------


def test_merge_keeps_the_winner_and_fills_nulls_from_later_rows(tmp_path):
    """Winner = lowest id; a NULL in the winner is filled from the next row in id order."""
    db = _legacy_db_with(
        tmp_path / "merge.db",
        [
            ("Cup of Joe", "Multo", None, None),           # id 1: the winner, both NULL
            ("Cup of Joe", "Multo", "mbid-1", None),       # id 2: supplies recording_mbid
            ("Cup of Joe", "Multo", None, "/music/c.mp3"),  # id 3: supplies file_path
            ("cup of joe", "Multo", "mbid-2", "/music/d.mp3"),  # id 4: loses its own path
        ],
    )
    DatabaseConnection(db).initialize()

    assert _rows(db) == [(1, "Cup of Joe", "Multo", "mbid-1", "/music/c.mp3")]


def test_merge_reports_only_paths_that_were_really_dropped(tmp_path):
    """A losing row is reported only when its path is non-NULL and differs from the kept one."""
    db = _legacy_db_with(
        tmp_path / "merge.db",
        [
            ("Cup of Joe", "Multo", None, None),
            ("Cup of Joe", "Multo", "mbid-1", None),
            ("Cup of Joe", "Multo", None, "/music/c.mp3"),
            ("cup of joe", "Multo", "mbid-2", "/music/d.mp3"),
        ],
    )
    DatabaseConnection(db).initialize()

    lines = (tmp_path / "migration-duplicates-1.txt").read_text(encoding="utf-8").splitlines()
    # The path that was kept as the winner's own is not reported, and neither is a NULL.
    assert lines == ["4\tcup of joe\tMulto\t/music/d.mp3"]


def test_a_legacy_database_without_duplicates_gets_no_report(tmp_path):
    db = _legacy_db_with(
        tmp_path / "clean.db",
        [("Autechre", "Amber", None, "/m/1.mp3"), ("Autechre", "Nil", None, "/m/2.mp3")],
    )
    DatabaseConnection(db).initialize()

    assert len(_rows(db)) == 2
    assert not (tmp_path / "migration-duplicates-1.txt").exists()


# --------------------------------------------------------------------------
# Fresh versus existing: the same end shape, different amount of work.
# --------------------------------------------------------------------------


def test_fresh_database_is_created_at_the_current_version_with_the_natural_key(tmp_path):
    db = tmp_path / "fresh.db"
    conn = DatabaseConnection(db)
    conn.initialize()

    assert _user_version(db) == migrations.SCHEMA_VERSION == 2
    # The natural key is the unique index, not a table constraint that cannot see
    # case differences or distinguish two NULL mbids from each other.
    assert "UNIQUE(artist, title, recording_mbid)" not in _table_sql(db, "local_library")
    with conn.get_connection() as c:
        assert c.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'ux_local_library_natural'"
        ).fetchone()
    # Nothing to dedupe, so no report -- and no backup of an empty file.
    assert not (tmp_path / "migration-duplicates-1.txt").exists()
    assert not (tmp_path / "migration-duplicates-2.txt").exists()
    assert not list(tmp_path.glob("fresh.db.backup-*"))


def test_already_current_database_is_neither_backed_up_nor_migrated(tmp_path):
    db = tmp_path / "t.db"
    conn = DatabaseConnection(db)
    conn.initialize()
    with conn.get_connection() as c:
        c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('A','B','x')")
        c.commit()

    conn.initialize()  # a second start, with the schema already at v1

    assert not list(tmp_path.glob("t.db.backup-*"))
    assert _user_version(db) == 2
    assert len(_rows(db)) == 1


def test_migrating_an_existing_database_backs_the_old_file_up_first(tmp_path):
    db = _legacy_db(tmp_path)
    DatabaseConnection(db).initialize()

    backups = list(tmp_path.glob("legacy.db.backup-*"))
    assert len(backups) == 1, "exactly one backup, taken by the one migration that ran"
    c = sqlite3.connect(backups[0])
    try:
        # The backup holds the pre-migration rows, not the deduped result.
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 4
    finally:
        c.close()


# --------------------------------------------------------------------------
# Atomicity: a failure after part of the work is done changes nothing.
# --------------------------------------------------------------------------


def test_failure_after_the_rebuild_leaves_the_legacy_table_untouched(tmp_path, monkeypatch):
    """The dedupe and the rebuild run before the version is written; both must roll back."""
    db = _legacy_db(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(migrations, "_set_user_version", boom)
    with pytest.raises(RuntimeError):
        DatabaseConnection(db).initialize()

    assert _user_version(db) == 0
    assert len(_rows(db)) == 4
    # The rolled-back rebuild left the legacy constraint and no new index behind.
    assert "UNIQUE(artist, title, recording_mbid)" in _table_sql(db, "local_library")
    with sqlite3.connect(db) as c:
        assert not c.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'ux_local_library_natural'"
        ).fetchone()
    # The report is written last, so a failed migration leaves no misleading report.
    assert not (tmp_path / "migration-duplicates-1.txt").exists()


class _CommitFailsConnection:
    """A real connection whose ``COMMIT`` fails the way a full disk makes it fail.

    Every statement of the migration has already run when this is called, so the
    rollback that follows puts the merged-away rows back exactly as they were.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *params):
        return self._conn.execute(sql, *params)

    def commit(self):
        raise sqlite3.OperationalError("disk I/O error")

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_commit_that_fails_leaves_no_report_describing_rows_that_remain(tmp_path):
    """The report must never name paths the database still references.

    A ``COMMIT`` can fail after every statement has run, and then the rollback
    puts the duplicate rows back. A report written before the commit would call
    that rolled-back state "dropped", and the one thing an operator does with
    this file is delete the paths it lists -- paths the database still points at.
    So the report is written only after a successful commit.
    """
    db = _legacy_db(tmp_path)
    raw = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrations.apply_migrations(_CommitFailsConnection(raw), db)
    finally:
        raw.close()

    assert not (tmp_path / "migration-duplicates-1.txt").exists(), (
        "the report names a duplicate that the rollback put back: its paths are "
        "still referenced by the database"
    )
    assert _user_version(db) == 0
    assert len(_rows(db)) == 4


def test_apply_migrations_reports_the_version_it_ran(tmp_path):
    db = _legacy_db(tmp_path)
    conn = DatabaseConnection(db)
    with conn.get_connection() as c:
        assert migrations.apply_migrations(c, db) == [1, 2]
        assert migrations.apply_migrations(c, db) == []


# --------------------------------------------------------------------------
# Mutual exclusion: the migration really holds the write lock while it works.
# --------------------------------------------------------------------------

#: Asks a *separate process* whether it can start writing. A process of its own
#: matters: checking from this one would open and close a descriptor on the
#: database, and POSIX record locks -- the locks SQLite uses -- are dropped when
#: a process closes any descriptor to the locked file.
_LOCK_PROBE = (
    "import sqlite3, sys\n"
    "c = sqlite3.connect(sys.argv[1], timeout=0.2)\n"
    "try:\n"
    "    c.execute('BEGIN IMMEDIATE')\n"
    "    print('free')\n"
    "except sqlite3.OperationalError:\n"
    "    print('locked')\n"
)


def test_the_migration_holds_the_write_lock_while_it_runs(tmp_path, monkeypatch):
    """A second starter must not be able to write while the migration is in flight.

    This is the invariant ``BEGIN IMMEDIATE`` is supposed to buy. It has a
    non-obvious enemy: the pre-migration backup copies the database, and that
    copy closes a descriptor on the file inside the process holding the lock,
    which silently releases SQLite's lock. Both starters then migrate on top of
    each other -- one of them failing at COMMIT with ``disk I/O error``, or the
    duplicate report being written by a run that never committed.
    """
    db = _legacy_db(tmp_path)
    probes = []

    real_migrate_v1 = migrations._migrate_v1

    def probe_then_migrate(conn, db_path):
        result = subprocess.run(
            [sys.executable, "-c", _LOCK_PROBE, str(db)],
            capture_output=True, text=True, timeout=60,
        )
        probes.append(result.stdout.strip())
        return real_migrate_v1(conn, db_path)

    monkeypatch.setattr(migrations, "_migrate_v1", probe_then_migrate)

    DatabaseConnection(db).initialize()

    assert probes == ["locked"], (
        "another process could begin a write transaction while the migration "
        "was running, so the version check no longer serializes migrations"
    )
    # And the migration still did its job under that lock.
    assert _user_version(db) == 2
    assert len(_rows(db)) == 1


class _LockedBeginConnection:
    """A real connection whose ``BEGIN IMMEDIATE`` answers the way a locked peer does.

    ``BEGIN IMMEDIATE`` waits the connection's ``busy_timeout`` and then gives up
    with ``database is locked``; a stand-in keeps the test instant and hermetic
    instead of parking a real writer for five seconds.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *params):
        if sql.strip().upper().startswith("BEGIN IMMEDIATE"):
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_begin_that_cannot_take_the_lock_leaves_no_backup(tmp_path, caplog):
    """A migration that never began must not leave ``db.backup-*`` behind.

    The copy is taken before the lock on purpose, so this is the one path where a
    backup exists for a migration that never ran. Leaving it there would break
    what ``db.backup-*`` means -- "a migration ran" -- and present a copy nothing
    needs as the pre-migration state. The failure itself stays fatal on purpose
    (and is logged, not silent): an unmigrated schema must not be reported as a
    migrated one, because the process would then run against the legacy table.
    """
    db = _legacy_db(tmp_path)
    raw = sqlite3.connect(db)
    try:
        with caplog.at_level(logging.WARNING, logger="lyra.database.migrations"):
            with pytest.raises(sqlite3.OperationalError):
                migrations.apply_migrations(_LockedBeginConnection(raw), db)
    finally:
        raw.close()

    assert not list(tmp_path.glob("legacy.db.backup-*")), (
        "a backup survived a migration that never began, so a backup no longer "
        "means a migration ran"
    )
    assert "lock" in caplog.text.lower(), "the lock failure must not be silent"
    # No transaction was ever opened, so nothing was written and nothing is stale.
    assert _user_version(db) == 0
    assert len(_rows(db)) == 4


def test_a_backup_taken_for_a_migration_another_process_ran_is_dropped(tmp_path, monkeypatch):
    """A waiting process backs up, then finds the work done: it keeps no copy.

    Two starters both pass the cheap version check, so both copy the file before
    the lock. Only one migrates. The loser's copy was taken while the winner was
    still on its way, so it is not the pre-migration state a backup is supposed
    to hold; leaving it behind would present it as one.
    """
    db = _legacy_db(tmp_path)
    copies = []
    real_backup = migrations._backup_before_migrating
    racing = {"on": True}

    def backup_then_let_another_starter_finish(conn, db_path):
        path = real_backup(conn, db_path)
        copies.append(path)
        if racing["on"]:
            # The other starter wins the race between this check and the lock.
            racing["on"] = False
            DatabaseConnection(db).initialize()
        return path

    monkeypatch.setattr(
        migrations, "_backup_before_migrating", backup_then_let_another_starter_finish
    )

    with DatabaseConnection(db).get_connection() as conn:
        assert migrations.apply_migrations(conn, db) == []

    assert _user_version(db) == 2
    assert len(_rows(db)) == 1
    loser_copy, winner_copy = copies
    assert not loser_copy.exists(), (
        "the process that did not migrate kept a copy that is not a pre-migration backup"
    )
    assert winner_copy.exists(), "the one migration that ran lost its backup"
    assert len(list(tmp_path.glob("legacy.db.backup-*"))) == 1


# --------------------------------------------------------------------------
# Migration v2: the tracked_tracks state machine, the legacy status mapping,
# and the merge of rows that share the natural key.
#
# The helper below builds a database in exactly the v1 shape this branch
# released (``user_version = 1``), so the v1 -> v2 step is tested as an operator
# would meet it. Building it by hand also keeps the test independent of the code
# under test: a broken v1 could not quietly define its own fixture.
# --------------------------------------------------------------------------

V1 = """
CREATE TABLE artists (
    artist_name TEXT PRIMARY KEY, added_at TEXT NOT NULL,
    fallback_done INTEGER DEFAULT 0, recommended_tracks TEXT);
CREATE TABLE tracked_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, recording_mbid TEXT,
    artist TEXT NOT NULL, title TEXT NOT NULL,
    source TEXT DEFAULT 'listenbrainz', status TEXT DEFAULT 'PENDING',
    added_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(recording_mbid, artist, title));
CREATE TABLE local_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT, artist TEXT NOT NULL, album TEXT,
    title TEXT NOT NULL, recording_mbid TEXT, file_path TEXT, added_at TEXT NOT NULL);
CREATE UNIQUE INDEX ux_local_library_natural
    ON local_library(artist COLLATE NOCASE, title COLLATE NOCASE);
CREATE INDEX idx_local_library_artist ON local_library(artist);
CREATE INDEX idx_local_library_mbid ON local_library(recording_mbid);
CREATE INDEX idx_tracked_tracks_status ON tracked_tracks(status);
PRAGMA user_version = 1;
"""

#: The columns migration v2 adds to ``tracked_tracks``.
TRACKED_STATE_COLUMNS = (
    "attempts",
    "last_attempt_at",
    "next_retry_at",
    "last_error",
    "lidarr_checked_at",
)

#: The merge precedence, strongest first. A row with an earlier status wins.
STATUS_PRECEDENCE = (
    "COMPLETED",
    "RUNNING",
    "QUEUED_FALLBACK",
    "WAITING_LIDARR",
    "NEW",
    "FAILED",
    "DEAD",
)


def _v1_db(tmp_path, name="v1.db"):
    db = tmp_path / name
    c = sqlite3.connect(db)
    c.executescript(V1)   # executescript hanya untuk MENYIAPKAN data uji
    c.commit()
    c.close()
    return db


def _track(db, rows):
    """Insert ``(artist, title, status, recording_mbid)`` rows into tracked_tracks."""
    c = sqlite3.connect(db)
    c.executemany(
        "INSERT INTO tracked_tracks (recording_mbid, artist, title, status,"
        " added_at, updated_at) VALUES (?,?,?,?,?,?)",
        [(mbid, artist, title, status, NOW, NOW) for artist, title, status, mbid in rows],
    )
    c.commit()
    c.close()
    return db


def _tracked_rows(db):
    c = sqlite3.connect(db)
    try:
        return c.execute(
            "SELECT id, recording_mbid, artist, title, status, attempts, last_error"
            " FROM tracked_tracks ORDER BY id"
        ).fetchall()
    finally:
        c.close()


def _tracked_count(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT COUNT(*) FROM tracked_tracks").fetchone()[0]
    finally:
        c.close()


def _columns(db, table):
    c = sqlite3.connect(db)
    try:
        return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
    finally:
        c.close()


def _snapshot(db):
    """Everything a second v2 run must not change, as plain tuples."""
    c = sqlite3.connect(db)
    try:
        tracked = c.execute(
            "SELECT id, recording_mbid, artist, title, source, status, attempts,"
            " last_attempt_at, next_retry_at, last_error, lidarr_checked_at,"
            " added_at, updated_at FROM tracked_tracks ORDER BY id"
        ).fetchall()
        artists = c.execute("SELECT * FROM artists ORDER BY artist_name").fetchall()
        return tracked, artists
    finally:
        c.close()


def test_legacy_pending_status_becomes_new(tmp_path):
    """PENDING is the legacy name of the queue head; COMPLETED keeps its meaning.

    Two identical PENDING rows are inserted on purpose: ``COUNT(*)`` is asserted
    here because a ``dict(artist -> status)`` would collapse them and pass even
    if the merge never ran.
    """
    db = tmp_path / "legacy2.db"
    c = sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE tracked_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recording_mbid TEXT, artist TEXT NOT NULL,
        title TEXT NOT NULL, source TEXT DEFAULT 'listenbrainz', status TEXT DEFAULT 'PENDING',
        added_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(recording_mbid, artist, title));
    """)
    for _ in range(2):
        c.execute("INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
                  " VALUES ('A','B','PENDING','x','x')")
    c.execute("INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
              " VALUES ('C','D','COMPLETED','x','x')")
    c.commit()
    c.close()

    conn = DatabaseConnection(db)
    conn.initialize()
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM tracked_tracks").fetchone()[0] == 2
        rows = dict(c.execute("SELECT artist, status FROM tracked_tracks").fetchall())
        assert rows == {"A": "NEW", "C": "COMPLETED"}
        cols = {r[1] for r in c.execute("PRAGMA table_info(tracked_tracks)")}
        assert set(TRACKED_STATE_COLUMNS) <= cols
    # The duplicate that was merged away is named in the v2 report, not in v1's.
    assert _user_version(db) == 2
    lines = (tmp_path / "migration-duplicates-2.txt").read_text(encoding="utf-8").splitlines()
    assert lines == ["2\tA\tB"]
    assert not (tmp_path / "migration-duplicates-1.txt").exists()


def test_tracked_tracks_gets_the_retry_columns_with_legacy_defaults(tmp_path):
    """Legacy rows arrive with no attempt counted and no retry scheduled."""
    db = _track(_v1_db(tmp_path), [("A", "B", "PENDING", None)])
    DatabaseConnection(db).initialize()

    assert _columns(db, "tracked_tracks") >= set(TRACKED_STATE_COLUMNS)
    with DatabaseConnection(db).get_connection() as c:
        row = c.execute(
            "SELECT attempts, last_attempt_at, next_retry_at, last_error,"
            " lidarr_checked_at FROM tracked_tracks"
        ).fetchone()
        assert tuple(row) == (0, None, None, None, None)
        # ``attempts`` is NOT NULL DEFAULT 0, so a row that names no attempts has one.
        c.execute(
            "INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
            " VALUES ('X','Y','NEW','x','x')"
        )
        assert c.execute(
            "SELECT attempts FROM tracked_tracks WHERE artist = 'X'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("winner,loser", list(zip(STATUS_PRECEDENCE, STATUS_PRECEDENCE[1:])))
def test_status_precedence_decides_which_row_survives(tmp_path, winner, loser):
    """The weaker status has the lower id, so only the precedence can pick the winner."""
    db = _track(
        _v1_db(tmp_path),
        [("A", "B", loser, "mbid-loser"), ("A", "B", winner, "mbid-winner")],
    )
    DatabaseConnection(db).initialize()

    assert _tracked_count(db) == 1
    row = _tracked_rows(db)[0]
    assert (row[3], row[4]) == ("B", winner)
    assert row[1] == "mbid-winner", "the winner keeps its own attributes"
    assert (tmp_path / "migration-duplicates-2.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["1\tA\tB"]


def test_dedupe_keeps_the_lowest_id_when_the_statuses_tie_and_folds_case(tmp_path):
    """Same status, three spellings of the natural key: the lowest id survives."""
    db = _track(
        _v1_db(tmp_path),
        [
            ("Autechre", "Amber", "NEW", "mbid-1"),
            ("autechre", "AMBER", "NEW", "mbid-2"),
            ("AUTECHRE", "amber", "NEW", "mbid-3"),
        ],
    )
    DatabaseConnection(db).initialize()

    assert _tracked_count(db) == 1
    assert _tracked_rows(db) == [(1, "mbid-1", "Autechre", "Amber", "NEW", 0, None)]


def test_merge_takes_the_highest_attempts_and_the_winners_last_error(tmp_path):
    """``attempts = MAX(attempts)``; ``last_error`` follows the winner, not the max."""
    db = tmp_path / "merge.db"
    c = sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE tracked_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recording_mbid TEXT, artist TEXT NOT NULL,
        title TEXT NOT NULL, source TEXT DEFAULT 'listenbrainz',
        status TEXT DEFAULT 'PENDING', added_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT, next_retry_at TEXT,
        last_error TEXT, lidarr_checked_at TEXT);
    """)
    c.executemany(
        "INSERT INTO tracked_tracks (artist,title,status,attempts,last_error,"
        " added_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        [
            # id 1: five failed attempts, the loser on status
            ("A", "B", "FAILED", 5, "yt-dlp timed out", NOW, NOW),
            # id 2: completed, but only two attempts of its own
            ("A", "B", "COMPLETED", 2, "tagging failed", NOW, NOW),
        ],
    )
    c.commit()
    c.execute("BEGIN IMMEDIATE")
    dropped = migrations._merge_tracked_tracks(c)
    c.commit()
    try:
        assert c.execute("SELECT COUNT(*) FROM tracked_tracks").fetchone()[0] == 1
        row = c.execute(
            "SELECT id, status, attempts, last_error FROM tracked_tracks"
        ).fetchone()
        assert tuple(row) == (2, "COMPLETED", 5, "tagging failed")
        assert dropped == ["1\tA\tB"]
    finally:
        c.close()


def test_unique_natural_index_rejects_a_duplicate_tracked_track(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as c:
        c.execute(
            "INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
            " VALUES ('A','B','NEW','x','x')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
                " VALUES ('a','b','NEW','y','y')"
            )


def test_queue_index_covers_status_and_next_retry_at(tmp_path):
    """The worker picks its next track with ``ORDER BY next_retry_at``."""
    db = tmp_path / "t.db"
    DatabaseConnection(db).initialize()
    c = sqlite3.connect(db)
    try:
        assert c.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_tracked_queue'"
        ).fetchone()
        assert [row[2] for row in c.execute("PRAGMA index_info(ix_tracked_queue)")] == [
            "status",
            "next_retry_at",
        ]
    finally:
        c.close()


def test_a_v1_database_reaches_v2_without_v1_running_again(tmp_path, monkeypatch):
    """A database already at v1 must be carried to v2 by the new step alone.

    The version check is what makes this true, and it is exactly the case a
    deployed database is in: v1 already ran and must never run twice.
    """
    db = _track(_v1_db(tmp_path), [("A", "B", "PENDING", None)])

    def v1_must_not_run(*_args, **_kwargs):
        raise AssertionError("migration v1 ran again on an already migrated database")

    monkeypatch.setattr(migrations, "_migrate_v1", v1_must_not_run)

    DatabaseConnection(db).initialize()

    assert _user_version(db) == 2
    assert _tracked_rows(db) == [(1, None, "A", "B", "NEW", 0, None)]


def test_running_the_v2_step_twice_changes_nothing(tmp_path):
    db = _track(
        _v1_db(tmp_path),
        [("A", "B", "PENDING", None), ("A", "B", "COMPLETED", "mbid-2")],
    )
    conn = DatabaseConnection(db)
    with conn.get_connection() as c:
        assert migrations.apply_migrations(c, db) == [2]
    before = _snapshot(db)

    with conn.get_connection() as c:
        assert migrations.apply_migrations(c, db) == []

    assert _snapshot(db) == before
    assert _user_version(db) == 2


def test_artists_is_rebuilt_without_fallback_done_and_keeps_its_rows(tmp_path):
    """``fallback_done`` is dropped by rebuilding, not by ``DROP COLUMN``."""
    db = _v1_db(tmp_path)
    c = sqlite3.connect(db)
    c.execute(
        "INSERT INTO artists (artist_name, added_at, fallback_done, recommended_tracks)"
        " VALUES ('Autechre','2026-01-01',1,'[\"Amber\"]')"
    )
    c.commit()
    c.close()

    DatabaseConnection(db).initialize()

    assert "fallback_done" not in _columns(db, "artists")
    with DatabaseConnection(db).get_connection() as c:
        assert tuple(
            c.execute(
                "SELECT artist_name, added_at, recommended_tracks FROM artists"
            ).fetchone()
        ) == ("Autechre", "2026-01-01", '["Amber"]')
        # The rebuild leaves no scaffolding table behind.
        assert not c.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'artists_v2'"
        ).fetchone()


def test_each_migration_writes_its_own_report(tmp_path):
    """A v0 -> v2 run drops file paths in v1's report and tracks in v2's.

    One file per version: an operator reading ``migration-duplicates-1.txt`` must
    be looking at audio paths v1 unindexed, not at v2's merged duplicate tracks,
    and neither migration may overwrite the other's findings.
    """
    db = _legacy_db_with(
        tmp_path / "both.db",
        [
            ("Cup of Joe", "Multo", None, "/music/a.mp3"),
            ("cup of joe", "Multo", None, "/music/b.mp3"),
        ],
    )
    c = sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE tracked_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recording_mbid TEXT, artist TEXT NOT NULL,
        title TEXT NOT NULL, source TEXT DEFAULT 'listenbrainz', status TEXT DEFAULT 'PENDING',
        added_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(recording_mbid, artist, title));
    """)
    c.executemany(
        "INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
        " VALUES ('Artist','Song',?,?,?)",
        [("PENDING", NOW, NOW), ("COMPLETED", NOW, NOW)],
    )
    c.commit()
    c.close()

    DatabaseConnection(db).initialize()

    assert (tmp_path / "migration-duplicates-1.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["2\tcup of joe\tMulto\t/music/b.mp3"]
    assert (tmp_path / "migration-duplicates-2.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["1\tArtist\tSong"]


def test_an_insert_that_omits_status_lands_as_new(tmp_path):
    """No writer may inherit the legacy ``'PENDING'`` default.

    A row inserted without naming a status must be one the queue reads. The v1
    DDL's ``DEFAULT 'PENDING'`` would make it a row nothing looks at -- a
    silently invisible track -- so v2 rebuilds the table without it.
    """
    db = tmp_path / "t.db"
    DatabaseConnection(db).initialize()

    c = sqlite3.connect(db)
    try:
        c.execute(
            "INSERT INTO tracked_tracks (artist,title,added_at,updated_at)"
            " VALUES ('A','B',?,?)",
            (NOW, NOW),
        )
        c.commit()
        assert c.execute("SELECT status FROM tracked_tracks").fetchone()[0] == "NEW"
        status = {
            row[1]: row for row in c.execute("PRAGMA table_info(tracked_tracks)")
        }["status"]
        assert status[3] == 1, "status must be NOT NULL"
        assert status[4] == "'NEW'", "status must default to the queue head"
    finally:
        c.close()


def test_v1_tracked_tracks_rows_and_ids_survive_the_rebuild(tmp_path):
    """The rebuild copies rows with their ids, and drops the legacy constraint.

    The duplicate report names rows by id, so an id must keep naming the same
    track. The gap left by an earlier delete stays a gap rather than being
    renumbered. The table-level ``UNIQUE(recording_mbid, artist, title)`` that
    v1 released cannot be dropped in place, so the rebuild is what removes it.
    """
    db = _track(
        _v1_db(tmp_path),
        [
            ("A", "B", "PENDING", None),
            ("Deleted", "Before Migrating", "NEW", None),
            ("C", "D", "COMPLETED", "mbid-2"),
        ],
    )
    c = sqlite3.connect(db)
    c.execute("DELETE FROM tracked_tracks WHERE id = 2")
    c.commit()
    c.close()

    DatabaseConnection(db).initialize()

    assert _tracked_rows(db) == [
        (1, None, "A", "B", "NEW", 0, None),
        (3, "mbid-2", "C", "D", "COMPLETED", 0, None),
    ]
    indexes = _index_names(db, "tracked_tracks")
    assert not any(name.startswith("sqlite_autoindex_") for name in indexes), (
        "the legacy UNIQUE(recording_mbid, artist, title) must not survive the rebuild"
    )
    assert "idx_tracked_tracks_status" not in indexes, (
        "ix_tracked_queue's prefix covers it"
    )
    assert {"ux_tracked_natural", "ix_tracked_queue"} <= indexes


def _index_names(db, table):
    c = sqlite3.connect(db)
    try:
        return {str(row[1]) for row in c.execute(f"PRAGMA index_list({table})")}
    finally:
        c.close()


def test_a_legacy_row_without_a_status_is_queued_as_new(tmp_path):
    """``status`` was nullable in v1; the NOT NULL rebuild must not crash on it.

    Such a row is invisible to every reader, which is the failure this iteration
    removes, so the rebuild parks it on the queue head instead of failing the
    whole migration on an integrity error.
    """
    db = _track(_v1_db(tmp_path), [("A", "B", "PENDING", None)])
    c = sqlite3.connect(db)
    c.execute(
        "INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
        " VALUES ('C','D',NULL,?,?)",
        (NOW, NOW),
    )
    c.commit()
    c.close()

    DatabaseConnection(db).initialize()

    assert [row[4] for row in _tracked_rows(db)] == ["NEW", "NEW"]
