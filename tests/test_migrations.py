"""Migrasi bernomor, kunci natural ``local_library``, dan laporan duplikat.

The tests below pin four separate promises:

* an existing database is migrated in place, duplicates merged by the natural
  key ``(artist COLLATE NOCASE, title COLLATE NOCASE)``, with the paths that
  were dropped reported next to the file;
* a fresh database is created directly at the current version, with the same
  shape and without a pointless backup or an empty report;
* a migration either happens or does not: a failure, including one after part
  of the work is done, leaves the database exactly as it was;
* the version check is the one that decides, so a second process (or the next
  startup) skips the work instead of repeating it.
"""

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
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1
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


def test_fresh_database_is_created_at_version_1_with_the_natural_key(tmp_path):
    db = tmp_path / "fresh.db"
    conn = DatabaseConnection(db)
    conn.initialize()

    assert _user_version(db) == 1
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
    assert _user_version(db) == 1
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


def test_apply_migrations_reports_the_version_it_ran(tmp_path):
    db = _legacy_db(tmp_path)
    conn = DatabaseConnection(db)
    with conn.get_connection() as c:
        assert migrations.apply_migrations(c, db) == [1]
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
        real_migrate_v1(conn, db_path)

    monkeypatch.setattr(migrations, "_migrate_v1", probe_then_migrate)

    DatabaseConnection(db).initialize()

    assert probes == ["locked"], (
        "another process could begin a write transaction while the migration "
        "was running, so the version check no longer serializes migrations"
    )
    # And the migration still did its job under that lock.
    assert _user_version(db) == 1
    assert len(_rows(db)) == 1


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

    assert _user_version(db) == 1
    assert len(_rows(db)) == 1
    loser_copy, winner_copy = copies
    assert not loser_copy.exists(), (
        "the process that did not migrate kept a copy that is not a pre-migration backup"
    )
    assert winner_copy.exists(), "the one migration that ran lost its backup"
    assert len(list(tmp_path.glob("legacy.db.backup-*"))) == 1
