"""
Canary for the ``isolated_db`` fixture: this proves the fixture, not production.

``Config.DB_PATH`` still defaults to the *relative* name ``music_automation.db``
(``src/lyra/config.py:41``); making it absolute is design section 8.6, deferred to
iteration 2. So running the application from a different working directory still
creates a second, empty database in that directory. These tests do not claim
otherwise, and they cannot catch that gap.

What they do prove is the other half: the autouse ``isolated_db`` fixture in
``conftest.py`` gives ``Config.DB_PATH`` a path under ``tmp_path``, so no test can
write the developer's real ``music_automation.db``. If the fixture is removed, or
stops redirecting the path, both tests below fail.
"""

from pathlib import Path

from lyra.config import Config
from lyra.database.connection import DatabaseConnection
from lyra.database.state import StateManager


def test_isolated_db_fixture_points_config_at_tmp_path(tmp_path):
    """Canary: the autouse fixture points ``Config.DB_PATH`` at ``tmp_path``."""
    db_path = Path(Config.DB_PATH)
    expected = tmp_path / "test.db"
    assert db_path == expected, (
        f"Config.DB_PATH is {Config.DB_PATH!r}, not the isolated_db fixture's "
        f"{str(expected)!r}; the fixture is no longer redirecting the store"
    )
    assert db_path.is_absolute(), f"Config.DB_PATH is {Config.DB_PATH!r}, not absolute"


def test_store_from_fixture_leaves_cwd_empty(tmp_path, monkeypatch):
    """Canary: a store built with no explicit path goes to ``tmp_path``, not the cwd."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    # No explicit path: exactly what StateManager() does in production.
    connection = DatabaseConnection()
    connection.initialize()
    StateManager(connection).add_local_track("Artist", "Title")

    # The store went to the fixture's database ...
    assert (tmp_path / "test.db").exists()
    # ... and left the working directory untouched.
    assert list(cwd.iterdir()) == []
