import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Point ``Config.DB_PATH`` at the test's own temporary directory.

    ``Config.DB_PATH`` is a relative name by default, and every store built
    without an explicit path resolves it against the working directory. Without
    this fixture a test that builds such a store writes ``music_automation.db``
    into whatever directory pytest was started from -- the repository root or a
    throwaway directory. It is autouse so no test has to remember it, and it is
    ``raising=False`` so it holds even if the attribute were ever removed.
    """
    from lyra.config import Config

    monkeypatch.setattr(Config, "DB_PATH", str(tmp_path / "test.db"), raising=False)
