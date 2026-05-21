import sqlite3
import datetime

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
    scanner = LibraryScanner(str(library_path), db_connection=db_connection)
    added = scanner.scan_library(force=True)

    state = StateManager(db_connection=db_connection)

    assert added == 1
    assert state.track_exists(artist="Sample Artist", title="Example Track")


def test_add_tracked_track_does_not_duplicate(tmp_path):
    db_path = tmp_path / "test.db"
    db_connection = DatabaseConnection(str(db_path))
    track_manager = TrackStateManager(db_connection=db_connection)

    first = track_manager.add_tracked_track(None, "Artist", "Title")
    second = track_manager.add_tracked_track(None, "Artist", "Title")

    assert first is True
    assert second is False


def test_get_pending_tracked_tracks_returns_overdue(tmp_path):
    db_path = tmp_path / "test.db"
    db_connection = DatabaseConnection(str(db_path))
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
