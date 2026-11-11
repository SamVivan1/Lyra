"""
Tests that the suite is hermetic: no test leaves a database in the working directory.

``Config.DB_PATH`` defaults to the relative name ``music_automation.db``, so any
code path that builds a store without an explicit path would write that file next
to whatever directory pytest was started from -- the repository root, or a
throwaway directory a developer brought the suite into. The autouse ``isolated_db``
fixture in ``conftest.py`` is what prevents it; these tests are the proof.
"""

from pathlib import Path

from lyra.config import Config
from lyra.database.connection import DatabaseConnection
from lyra.database.state import StateManager


def test_isolated_db_points_config_away_from_cwd(tmp_path):
    """The autouse fixture must give ``Config.DB_PATH`` an absolute path."""
    db_path = Path(Config.DB_PATH)
    assert db_path.is_absolute(), (
        f"Config.DB_PATH is {Config.DB_PATH!r}, which resolves against the "
        "working directory; tests would write into the repository"
    )
    assert db_path != Path.cwd() / "music_automation.db"


def test_store_construction_writes_no_db_in_cwd(tmp_path, monkeypatch):
    """A store built the way production builds it must not touch the cwd."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    # No explicit path: exactly what StateManager() does in production.
    connection = DatabaseConnection()
    connection.initialize()
    StateManager(connection).add_local_track("Artist", "Title")

    assert not (cwd / "music_automation.db").exists()
    assert list(cwd.iterdir()) == []
