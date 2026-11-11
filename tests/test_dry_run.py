"""Dry run must be a property of the store, not a flag passed around.

A ``--dry-run`` run wrote rows in the field: the decision was taken at the call
site, so a write path that forgot to look at the flag still wrote. These tests
hold the flag on the objects that own the writes and assert on the database and
on the absence of the external call, not on the log line alone.
"""

import logging
import sqlite3
from unittest.mock import MagicMock

import pytest

from lyra.config import Config
from lyra.database.connection import DatabaseConnection
from lyra.database.library_scanner import LibraryScanner
from lyra.database.state import StateManager
from lyra.database.track_manager import TrackStateManager
from lyra.downloader.downloader import Downloader
from lyra.downloader.fallback_orchestrator import FallbackOrchestrator
from lyra.orchestrator import ApplicationOrchestrator


def _count(conn, table):
    """Count rows in one table of the test database."""
    with conn.get_connection() as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
def db(tmp_path):
    """An initialized database of its own, never the working directory one."""
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    return conn


def test_dry_run_store_writes_nothing(db):
    """Both stores no-op and report success, as a successful write would."""
    sm = StateManager(db, dry_run=True)
    tm = TrackStateManager(db, dry_run=True)

    assert sm.add_local_track("A", "B") is True
    assert tm.add_tracked_track(None, "A", "B") is True
    assert sm.add_artist("A", recommended_tracks=["B"]) is True
    assert tm.mark_tracked_track_completed(artist="A", title="B") is True
    assert sm.clear_local_library() is True

    assert _count(db, "local_library") == 0
    assert _count(db, "tracked_tracks") == 0
    assert _count(db, "artists") == 0


def test_dry_run_store_does_not_open_a_write_connection(db, monkeypatch):
    """The no-op happens before a connection is opened to write."""

    def forbidden(*args, **kwargs):
        raise AssertionError("a dry-run store opened a connection to write")

    monkeypatch.setattr(db, "get_connection", forbidden)
    sm = StateManager(db, dry_run=True)
    tm = TrackStateManager(db, dry_run=True)

    assert sm.add_local_track("A", "B") is True
    assert tm.add_tracked_track(None, "A", "B") is True


def test_dry_run_rescan_writes_nothing(db, tmp_path, monkeypatch):
    """``--rescan --dry-run`` must neither add rows nor clear the library."""
    album = tmp_path / "Music" / "A" / "B"
    album.mkdir(parents=True)
    (album / "S.mp3").write_bytes(b"\x00")
    monkeypatch.setattr(Config, "LIBRARY_PATH", str(tmp_path / "Music"), raising=False)

    StateManager(db, dry_run=True).ensure_library_indexed(force_rescan=True)

    assert _count(db, "local_library") == 0


def test_dry_run_rescan_does_not_clear_an_indexed_library(db, tmp_path, monkeypatch):
    """A dry-run force rescan leaves the existing index exactly as it was."""
    album = tmp_path / "Music" / "A" / "B"
    album.mkdir(parents=True)
    (album / "S.mp3").write_bytes(b"\x00")
    monkeypatch.setattr(Config, "LIBRARY_PATH", str(tmp_path / "Music"), raising=False)

    StateManager(db).ensure_library_indexed(force_rescan=True)
    assert _count(db, "local_library") == 1

    StateManager(db, dry_run=True).ensure_library_indexed(force_rescan=True)
    assert _count(db, "local_library") == 1


def test_library_scanner_dry_run_writes_nothing(db, tmp_path):
    """The scanner carries its own flag, so it cannot write through its store."""
    album = tmp_path / "Music" / "A" / "B"
    album.mkdir(parents=True)
    (album / "S.mp3").write_bytes(b"\x00")

    added = LibraryScanner(str(tmp_path / "Music"), db, dry_run=True).scan_library(force=True)

    assert added == 1  # simulated, as a successful index would report
    assert _count(db, "local_library") == 0


def test_dry_run_download_manual_spawns_no_subprocess(tmp_path, monkeypatch):
    """``download_manual`` returns True and never starts yt-dlp."""
    spawned = []

    def record(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("dry run started a subprocess")

    monkeypatch.setattr("subprocess.run", record)
    monkeypatch.setattr("subprocess.Popen", record)

    d = Downloader(library_path=str(tmp_path), dry_run=True)

    assert d.download_manual("url", "A", "B") is True
    assert spawned == []
    # No folder or file was created either: a dry run does not write the disk.
    assert list(tmp_path.iterdir()) == []


def test_dry_run_download_track_reaches_download_manual(tmp_path, monkeypatch):
    """``download_track`` passes the dry flag down; it cannot be lost on the way."""
    spawned = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: spawned.append(a))

    d = Downloader(library_path=str(tmp_path), dry_run=True)

    assert d.download_track("A", "B") is True
    assert spawned == []

    # The call-level flag works too, without the store being constructed dry.
    other = Downloader(library_path=str(tmp_path))
    assert other.download_track("C", "D", dry_run=True) is True
    assert spawned == []


def test_dry_run_keeps_read_only_yt_dlp_calls_working(tmp_path, monkeypatch):
    """Search and video info stay usable: a dry run must be able to simulate."""
    payload = (
        '{"title": "A - B", "uploader": "A", "duration_string": "3:00",'
        ' "webpage_url": "https://example.invalid/watch?v=1"}\n'
    )

    class _Result:
        stdout = payload

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Result())

    d = Downloader(library_path=str(tmp_path), dry_run=True)

    assert d.get_search_results("a b")[0]["title"] == "A - B"
    assert d.get_video_info("https://example.invalid/watch?v=1")["title"] == "A - B"


def test_run_fallback_dry_run_downloads_nothing(db, tmp_path, monkeypatch):
    """A pending track is not downloaded, not indexed and not completed."""
    tm = TrackStateManager(db)
    tm.add_tracked_track(None, "Fallback Artist", "Fallback Song")
    with sqlite3.connect(str(tmp_path / "t.db")) as raw:
        raw.execute(
            "UPDATE tracked_tracks SET added_at = ?, updated_at = ?",
            ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
        )

    spawned = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: spawned.append(a))

    library = tmp_path / "Library"
    orchestrator = FallbackOrchestrator(
        state_manager=StateManager(db, dry_run=True),
        downloader=Downloader(library_path=str(library), dry_run=True),
    )
    orchestrator.run_fallback(dry_run=True)

    assert spawned == []
    assert _count(db, "local_library") == 0
    with db.get_connection() as c:
        status = c.execute("SELECT status FROM tracked_tracks").fetchone()[0]
    assert status == "NEW"
    assert not library.exists()


def test_run_fallback_passes_the_flag_it_received(db, tmp_path):
    """The flag must reach ``download_track``; it used to be hardcoded False."""
    tm = TrackStateManager(db)
    tm.add_tracked_track(None, "Fallback Artist", "Fallback Song")
    with sqlite3.connect(str(tmp_path / "t.db")) as raw:
        raw.execute(
            "UPDATE tracked_tracks SET added_at = ?, updated_at = ?",
            ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
        )

    downloader = MagicMock()
    downloader.download_track.return_value = True
    orchestrator = FallbackOrchestrator(
        state_manager=StateManager(db), downloader=downloader
    )

    orchestrator.run_fallback(dry_run=True)

    downloader.download_track.assert_called_once_with(
        "Fallback Artist", "Fallback Song", dry_run=True
    )
    assert _count(db, "local_library") == 0


def test_orchestrator_dry_run_skips_lidarr_and_writes_nothing(db, monkeypatch):
    """No row and no POST: the merchant of record is not touched either."""
    monkeypatch.setattr(Config, "validate", classmethod(lambda cls: None))
    orch = ApplicationOrchestrator(db, dry_run=True)
    orch.lb_client = MagicMock()
    orch.lb_client.get_recommended_artists_with_tracks.return_value = {
        "Artist A": ["Song 1", "Song 2"]
    }
    orch.lidarr_client = MagicMock()
    orch.lidarr_client.get_existing_artists.return_value = []

    added = orch.add_artists_from_recommendations(limit=5, dry_run=True)

    assert added == 1  # counted as added, simulation reports what would happen
    orch.lidarr_client.add_artist.assert_not_called()
    assert _count(db, "artists") == 0
    assert _count(db, "tracked_tracks") == 0
    assert _count(db, "local_library") == 0


def test_orchestrator_store_is_dry_without_the_call_flag(db, monkeypatch):
    """The constructor flag alone is enough: that is what the CLI passes."""
    monkeypatch.setattr(Config, "validate", classmethod(lambda cls: None))
    orch = ApplicationOrchestrator(db, dry_run=True)
    orch.lb_client = MagicMock()
    orch.lb_client.get_recommended_artists_with_tracks.return_value = {
        "Artist A": ["Song 1"]
    }
    orch.lidarr_client = MagicMock()
    orch.lidarr_client.get_existing_artists.return_value = []

    assert orch.add_artists_from_recommendations(limit=5) == 1

    orch.lidarr_client.add_artist.assert_not_called()
    assert _count(db, "artists") == 0
    assert _count(db, "tracked_tracks") == 0


def test_orchestrator_rejects_a_dry_run_with_a_wet_store(db):
    """The contradiction is refused at construction, not compensated later.

    A dry orchestrator writing through a wet store is the hole this task
    exists to close. Making the pair unconstructible closes it for every
    route at once, instead of each method having to remember to compensate.
    """
    with pytest.raises(ValueError) as excinfo:
        ApplicationOrchestrator(db, state_manager=StateManager(db), dry_run=True)

    message = str(excinfo.value)
    assert "state_manager" in message
    assert "dry_run=True" in message
    assert "dry_run=False" in message
    assert "StateManager" in message  # the fix, not only the contradiction


def test_orchestrator_dry_with_a_dry_store_keeps_the_indexed_library(
    db, tmp_path, monkeypatch
):
    """No compensation needed: the dry store the orchestrator holds cannot clear."""
    album = tmp_path / "Music" / "A" / "B"
    album.mkdir(parents=True)
    (album / "S.mp3").write_bytes(b"\x00")
    monkeypatch.setattr(Config, "LIBRARY_PATH", str(tmp_path / "Music"), raising=False)

    StateManager(db).ensure_library_indexed(force_rescan=True)
    assert _count(db, "local_library") == 1

    orch = ApplicationOrchestrator(
        db, state_manager=StateManager(db, dry_run=True), dry_run=True
    )
    orch.ensure_library_indexed(force_rescan=True)

    assert _count(db, "local_library") == 1


def test_orchestrator_dry_with_a_dry_store_downloads_nothing(
    db, tmp_path, monkeypatch
):
    """The fallback path of a dry orchestrator cannot reach a download."""
    tm = TrackStateManager(db)
    tm.add_tracked_track(None, "Fallback Artist", "Fallback Song")
    with sqlite3.connect(str(tmp_path / "t.db")) as raw:
        raw.execute(
            "UPDATE tracked_tracks SET added_at = ?, updated_at = ?",
            ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
        )

    spawned = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry run spawned")),
    )

    orch = ApplicationOrchestrator(
        db, state_manager=StateManager(db, dry_run=True), dry_run=True
    )
    orch.run_fallback()

    assert spawned == []
    assert _count(db, "local_library") == 0
    with db.get_connection() as c:
        status = c.execute("SELECT status FROM tracked_tracks").fetchone()[0]
    assert status == "NEW"


def test_fallback_dry_run_never_logs_a_real_completion(db, tmp_path, monkeypatch, caplog):
    """A simulated download must not end with "Fallback completed"."""
    tm = TrackStateManager(db)
    tm.add_tracked_track(None, "Fallback Artist", "Fallback Song")
    with sqlite3.connect(str(tmp_path / "t.db")) as raw:
        raw.execute(
            "UPDATE tracked_tracks SET added_at = ?, updated_at = ?",
            ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
        )

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry run spawned")),
    )

    orchestrator = FallbackOrchestrator(
        state_manager=StateManager(db, dry_run=True),
        downloader=Downloader(library_path=str(tmp_path / "Library"), dry_run=True),
    )
    with caplog.at_level(logging.INFO):
        orchestrator.run_fallback(dry_run=True)

    assert "[DRY RUN] Would have completed" in caplog.text
    assert "Fallback completed for" not in caplog.text


def test_orchestrator_wet_run_still_writes_and_adds(db, monkeypatch):
    """The single code path must still do the real work outside dry run."""
    monkeypatch.setattr(Config, "validate", classmethod(lambda cls: None))
    orch = ApplicationOrchestrator(db)
    orch.lb_client = MagicMock()
    orch.lb_client.get_recommended_artists_with_tracks.return_value = {
        "Artist A": ["Song 1"]
    }
    orch.lidarr_client = MagicMock()
    orch.lidarr_client.get_existing_artists.return_value = []
    orch.lidarr_client.add_artist.return_value = True

    assert orch.add_artists_from_recommendations(limit=5) == 1

    orch.lidarr_client.add_artist.assert_called_once_with("Artist A")
    assert _count(db, "artists") == 1
    assert _count(db, "tracked_tracks") == 1
