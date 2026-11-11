import sqlite3
from unittest.mock import MagicMock

from lyra.database.connection import DatabaseConnection
from lyra.database.state import StateManager
from lyra.database.track_manager import TrackStateManager
from lyra.downloader.fallback_orchestrator import FallbackOrchestrator


def test_run_fallback_completes_missing_track(tmp_path):
    db_path = tmp_path / "test.db"
    db_connection = DatabaseConnection(str(db_path))
    db_connection.initialize()
    state_manager = StateManager(db_connection=db_connection)
    track_manager = TrackStateManager(db_connection=db_connection)
    track_manager.add_tracked_track(None, "Fallback Artist", "Fallback Song")

    old_timestamp = "2000-01-01T00:00:00+00:00"
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tracked_tracks SET added_at = ?, updated_at = ?", (old_timestamp, old_timestamp))
        conn.commit()

    orchestrator = FallbackOrchestrator(state_manager=state_manager, downloader=MagicMock())
    orchestrator.downloader.download_track.return_value = True

    orchestrator.run_fallback(dry_run=False)

    assert state_manager.track_exists(artist="Fallback Artist", title="Fallback Song")
    pending = track_manager.get_pending_tracked_tracks(hours=0)
    assert pending == []


def test_run_fallback_skips_existing_local_track(tmp_path):
    db_path = tmp_path / "test.db"
    db_connection = DatabaseConnection(str(db_path))
    db_connection.initialize()
    state_manager = StateManager(db_connection=db_connection)
    track_manager = TrackStateManager(db_connection=db_connection)

    state_manager.add_local_track("Already There", "Existing Track", album="Album")
    track_manager.add_tracked_track(None, "Already There", "Existing Track")

    old_timestamp = "2000-01-01T00:00:00+00:00"
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tracked_tracks SET added_at = ?, updated_at = ?", (old_timestamp, old_timestamp))
        conn.commit()

    orchestrator = FallbackOrchestrator(state_manager=state_manager, downloader=MagicMock())

    orchestrator.run_fallback(dry_run=False)

    orchestrator.downloader.download_track.assert_not_called()
    pending = track_manager.get_pending_tracked_tracks(hours=0)
    assert pending == []
