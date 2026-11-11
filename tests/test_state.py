import datetime
import os
import sqlite3

from lyra.config import Config
from lyra.database.connection import DatabaseConnection
from lyra.database.library_scanner import LibraryScanner
from lyra.database.state import StateManager
from lyra.database.track_manager import TrackStateManager


def test_scan_library_infers_metadata_from_path(tmp_path):
    library_path = tmp_path / "Music"
    track_path = library_path / "Sample Artist" / "Sample Album" / "Example Track.mp3"
    track_path.parent.mkdir(parents=True, exist_ok=True)
    track_path.write_text("")

    db_path = tmp_path / "test.db"
    db_connection = DatabaseConnection(str(db_path))
    db_connection.initialize()
    scanner = LibraryScanner(str(library_path), db_connection=db_connection)
    added = scanner.scan_library(force=True)

    state = StateManager(db_connection=db_connection)

    assert added == 1
    assert state.track_exists(artist="Sample Artist", title="Example Track")


def test_parse_library_paths_multiple_paths():
    paths = Config.parse_library_paths(
        "/mnt/Storage2/data/media/Music:/mnt/Storage1/data/media/Music"
    )

    assert paths == [
        "/mnt/Storage2/data/media/Music",
        "/mnt/Storage1/data/media/Music"
    ]


def test_ensure_library_indexed_scans_multiple_roots(tmp_path, monkeypatch):
    root_a = tmp_path / "MusicA"
    root_b = tmp_path / "MusicB"
    track_a = root_a / "Sample Artist A" / "Album A" / "TrackA.mp3"
    track_b = root_b / "Sample Artist B" / "Album B" / "TrackB.mp3"
    track_a.parent.mkdir(parents=True, exist_ok=True)
    track_b.parent.mkdir(parents=True, exist_ok=True)
    track_a.write_text("")
    track_b.write_text("")

    db_path = tmp_path / "test.db"
    db_connection = DatabaseConnection(str(db_path))
    db_connection.initialize()
    monkeypatch.setattr(Config, "LIBRARY_PATH", f"{root_a}{os.pathsep}{root_b}")

    state = StateManager(db_connection=db_connection)
    state.ensure_library_indexed(force_rescan=True)

    assert state.track_exists(artist="Sample Artist A", title="TrackA")
    assert state.track_exists(artist="Sample Artist B", title="TrackB")


def test_add_tracked_track_does_not_duplicate(tmp_path):
    db_path = tmp_path / "test.db"
    db_connection = DatabaseConnection(str(db_path))
    db_connection.initialize()
    track_manager = TrackStateManager(db_connection=db_connection)

    first = track_manager.add_tracked_track(None, "Artist", "Title")
    second = track_manager.add_tracked_track(None, "Artist", "Title")

    assert first is True
    assert second is False


def test_get_pending_tracked_tracks_returns_overdue(tmp_path):
    db_path = tmp_path / "test.db"
    db_connection = DatabaseConnection(str(db_path))
    db_connection.initialize()
    track_manager = TrackStateManager(db_connection=db_connection)
    track_manager.add_tracked_track(None, "Artist", "Old Title")

    old_timestamp = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tracked_tracks SET added_at = ?, updated_at = ?", (old_timestamp, old_timestamp))
        conn.commit()

    pending = track_manager.get_pending_tracked_tracks(hours=24)

    assert len(pending) == 1
    assert pending[0]["artist"] == "Artist"
    assert pending[0]["title"] == "Old Title"


def test_add_tracked_track_is_idempotent(tmp_path):
    """Duplicate detection is the insert's job, not a separate read-then-write."""
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    tm = TrackStateManager(conn)
    assert tm.add_tracked_track(None, "A", "B") is True
    assert tm.add_tracked_track(None, "A", "B") is False
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM tracked_tracks").fetchone()[0] == 1


def test_add_artist_works_without_the_fallback_done_column(tmp_path):
    """The column is gone, so nothing may name it in an INSERT or a WHERE."""
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    state = StateManager(db_connection=conn)

    assert state.add_artist("Autechre", ["Amber"]) is True
    assert state.add_artist("Autechre", ["Amber"]) is False
    assert state.get_tracked_artists() == ["Autechre"]


def test_a_legacy_pending_track_is_queued_as_new(tmp_path):
    """A migrated ``PENDING`` row must still be found by the fallback queue.

    The mapping alone is not enough: if the queue kept asking for ``PENDING``
    while every writer says ``NEW``, the row the migration just rescued would be
    invisible and the track would never be downloaded.
    """
    db_path = tmp_path / "legacy.db"
    c = sqlite3.connect(db_path)
    c.executescript("""
    CREATE TABLE tracked_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recording_mbid TEXT, artist TEXT NOT NULL,
        title TEXT NOT NULL, source TEXT DEFAULT 'listenbrainz', status TEXT DEFAULT 'PENDING',
        added_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(recording_mbid, artist, title));
    """)
    c.execute("INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
              " VALUES ('Legacy Artist','Legacy Song','PENDING',?,?)",
              ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"))
    c.commit()
    c.close()

    conn = DatabaseConnection(db_path)
    conn.initialize()
    pending = TrackStateManager(conn).get_pending_tracked_tracks(hours=0)

    assert [t["artist"] for t in pending] == ["Legacy Artist"]
    assert [t["title"] for t in pending] == ["Legacy Song"]


def test_a_shared_mbid_does_not_make_two_tracks_the_same(tmp_path):
    """Identity is the natural key (artist + title); the MBID is an attribute.

    The old pre-check returned "already tracked" for any row carrying the same
    ``recording_mbid``, whatever artist and title it had. The unique index only
    judges artist + title, so the insert is the one thing that decides.
    """
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    tm = TrackStateManager(conn)

    assert tm.add_tracked_track("mbid-1", "A", "B") is True
    assert tm.add_tracked_track("mbid-1", "A", "Other Title") is True
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM tracked_tracks").fetchone()[0] == 2


def test_completion_by_artist_and_title_leaves_a_shared_mbid_alone(tmp_path):
    """Two rows may share one MBID (ruling 9), so completion must not key on it.

    The caller supplies artist + title as well; the natural key is the precise
    identifier and the shared MBID must not drag the other row to COMPLETED.
    """
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    tm = TrackStateManager(conn)
    assert tm.add_tracked_track("mbid-1", "A", "B") is True
    assert tm.add_tracked_track("mbid-1", "A", "Other Title") is True

    assert tm.mark_tracked_track_completed(
        recording_mbid="mbid-1", artist="A", title="B"
    ) is True

    with conn.get_connection() as c:
        statuses = dict(
            c.execute("SELECT title, status FROM tracked_tracks").fetchall()
        )
    assert statuses == {"B": "COMPLETED", "Other Title": "NEW"}


def test_completion_by_mbid_alone_still_works(tmp_path):
    """Without artist + title the MBID stays the fallback, and the method's
    public signature still accepts it."""
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    tm = TrackStateManager(conn)
    assert tm.add_tracked_track("mbid-1", "A", "B") is True

    assert tm.mark_tracked_track_completed(recording_mbid="mbid-1") is True
    with conn.get_connection() as c:
        assert c.execute("SELECT status FROM tracked_tracks").fetchone()[0] == "COMPLETED"


def test_an_insert_that_omits_status_is_visible_to_the_queue(tmp_path):
    """The default status must be one this query reads.

    v1's ``DEFAULT 'PENDING'`` would make a status-less INSERT a row the fallback
    queue never returns -- a silently invisible track. This is the end-to-end form
    of the schema assertion in ``test_migrations.py``.
    """
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    tm = TrackStateManager(conn)
    with conn.get_connection() as c:
        c.execute(
            "INSERT INTO tracked_tracks (artist,title,added_at,updated_at)"
            " VALUES ('A','B','2000-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00')"
        )
        c.commit()

    assert [t["title"] for t in tm.get_pending_tracked_tracks(hours=0)] == ["B"]
