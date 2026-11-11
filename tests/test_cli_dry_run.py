"""``--dry-run`` must reach the interactive mode.

The interactive mode is the one an operator reaches for to try things safely,
and it used to download, tag and write the database whatever the flag said: the
flag never left ``main``. These tests hold the flag on the object that owns the
behaviour, so no call site can lose it again.
"""

import builtins
import logging
import sys
from unittest.mock import MagicMock

import pytest

from lyra.cli.db_integration import CLIDatabaseIntegration
from lyra.cli.interactive import InteractiveDownloader
from lyra.config import Config
from lyra.database.connection import DatabaseConnection
from lyra.database.state import StateManager
from lyra.downloader.downloader import Downloader
from lyra.main import main, run_interactive_mode


@pytest.fixture(autouse=True)
def no_blocking_input(monkeypatch):
    """``_perform_download`` ends with a blocking ``input()``; tests cannot wait."""
    monkeypatch.setattr(builtins, "input", lambda *args, **kwargs: "")


@pytest.fixture
def db(tmp_path):
    """An initialized database of its own, never the working directory one."""
    connection = DatabaseConnection(tmp_path / "cli.db")
    connection.initialize()
    return connection


def _count(connection, table):
    """Count rows in one table of the test database."""
    with connection.get_connection() as cursor:
        return cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_interactive_passes_dry_run_to_downloader():
    """The flag must reach ``download_manual``; it used to be dropped here."""
    downloader = MagicMock()
    cli = InteractiveDownloader(
        downloader=downloader, db_integration=MagicMock(), dry_run=True
    )

    cli._perform_download("url", "A", "B")

    assert downloader.download_manual.call_args.kwargs["dry_run"] is True


def test_dry_interactive_download_records_nothing(db):
    """A dry session reports success and leaves the library table empty.

    The store it holds is deliberately wet: the interactive path must not write
    even then, because ``download_manual`` returns True for a simulated
    download and the old code read that as a real one.
    """
    downloader = MagicMock()
    downloader.download_manual.return_value = True
    integration = CLIDatabaseIntegration(db_connection=db)
    cli = InteractiveDownloader(
        downloader=downloader, db_integration=integration, dry_run=True
    )

    cli._perform_download("url", "A", "B")

    downloader.download_manual.assert_called_once()
    assert downloader.download_manual.call_args.kwargs["dry_run"] is True
    assert _count(db, "local_library") == 0


def test_dry_interactive_download_claims_no_success(db, capsys):
    """The operator must not read a simulated download as a real one."""
    downloader = MagicMock()
    downloader.download_manual.return_value = True
    cli = InteractiveDownloader(
        downloader=downloader,
        db_integration=CLIDatabaseIntegration(db_connection=db),
        dry_run=True,
    )

    cli._perform_download("url", "A", "B")

    output = capsys.readouterr().out
    assert "DRY RUN" in output
    assert "Download successful" not in output


def test_dry_session_prints_the_badge_once(db, monkeypatch, capsys):
    """One badge at the start of the session, so the mode cannot be mistaken."""
    cli = InteractiveDownloader(
        downloader=MagicMock(),
        db_integration=CLIDatabaseIntegration(db_connection=db),
        dry_run=True,
    )
    monkeypatch.setattr(cli, "_select_with_hjkl", lambda items, title: None)

    with pytest.raises(SystemExit):
        cli.run()

    assert capsys.readouterr().out.count("DRY RUN") == 1


def test_wet_session_prints_no_badge(db, monkeypatch, capsys):
    """The badge belongs to the dry run alone; a real session says nothing."""
    cli = InteractiveDownloader(
        downloader=MagicMock(),
        db_integration=CLIDatabaseIntegration(db_connection=db),
    )
    monkeypatch.setattr(cli, "_select_with_hjkl", lambda items, title: None)

    with pytest.raises(SystemExit):
        cli.run()

    assert "DRY RUN" not in capsys.readouterr().out


def test_dry_interactive_session_starts_no_subprocess(db, tmp_path, monkeypatch):
    """The real downloader, dry: no yt-dlp run, no folder, no row."""
    spawned = []

    def record(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("dry run started a subprocess")

    monkeypatch.setattr("subprocess.run", record)
    monkeypatch.setattr("subprocess.Popen", record)
    library = tmp_path / "Library"

    cli = InteractiveDownloader(
        downloader=Downloader(library_path=str(library), dry_run=True),
        db_integration=CLIDatabaseIntegration(db_connection=db),
        dry_run=True,
    )

    cli._perform_download("https://example.invalid/w", "A", "B")

    assert spawned == []
    assert not library.exists()
    assert _count(db, "local_library") == 0


def test_wet_interactive_download_still_records(db):
    """The single code path must still do the real work outside dry run."""
    downloader = MagicMock()
    downloader.download_manual.return_value = True
    cli = InteractiveDownloader(
        downloader=downloader, db_integration=CLIDatabaseIntegration(db_connection=db)
    )

    cli._perform_download("url", "A", "B")

    assert downloader.download_manual.call_args.kwargs["dry_run"] is False
    assert _count(db, "local_library") == 1


def test_interactive_mode_initialises_a_fresh_database(tmp_path, monkeypatch):
    """A fresh database is usable: the CLI branch initialises it before use.

    ``DatabaseConnection`` no longer creates the schema in its constructor, so
    the integration the interactive downloader builds is uninitialised until
    this branch calls ``initialize()``; without it the first record dies with
    ``no such table``.
    """
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(Config, "DB_PATH", str(db_path), raising=False)
    sessions = []
    monkeypatch.setattr(
        InteractiveDownloader, "run", lambda self: sessions.append(self)
    )

    assert run_interactive_mode(dry_run=False) == 0

    assert len(sessions) == 1
    session = sessions[0]
    assert session.db_integration.state_manager.db._initialized is True
    # The proof the bug is gone: a record against the fresh file reaches a table.
    assert session.db_integration.record_manual_download("A", "B") is True
    assert _count(session.db_integration.state_manager.db, "local_library") == 1


def test_interactive_mode_refuses_a_corrupt_database_with_advice(
    tmp_path, monkeypatch, caplog
):
    """A corrupt file gives the actionable line, not a traceback."""
    db_path = tmp_path / "broken.db"
    db_path.write_bytes(b"this is not a database, it is a file of words")
    monkeypatch.setattr(Config, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(
        InteractiveDownloader,
        "run",
        lambda self: pytest.fail("a session started on a corrupt database"),
    )

    with caplog.at_level(logging.ERROR):
        assert run_interactive_mode(dry_run=True) == 1

    assert "repair-db" in caplog.text


def test_main_dispatches_the_interactive_mode_dry_with_an_initialised_database(
    tmp_path, monkeypatch
):
    """``lyra -i --dry-run`` builds the downloader dry, on an initialised file."""
    for name in ("LISTENBRAINZ_USERNAME", "LISTENBRAINZ_API_TOKEN", "LIDARR_API_KEY"):
        monkeypatch.setattr(Config, name, None, raising=False)
    db_path = tmp_path / "main.db"
    monkeypatch.setattr(Config, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr("lyra.main.setup_logging", lambda *args, **kwargs: None)

    started = []

    class _StubInteractive:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            started.append(self)

        def run(self):
            pass

    monkeypatch.setattr("lyra.cli.InteractiveDownloader", _StubInteractive)
    monkeypatch.setattr(sys, "argv", ["lyra", "--interactive", "--dry-run"])

    assert main() == 0

    assert started[0].kwargs["dry_run"] is True
    integration = started[0].kwargs["db_integration"]
    assert integration.state_manager.db._initialized is True
    # The file it opens is the configured one, never the working directory one.
    assert integration.state_manager.db.path == db_path


def test_main_still_requires_the_credentials_for_the_batch_modes(
    tmp_path, monkeypatch, caplog
):
    """Per-mode validation must not weaken the batch modes."""
    for name in ("LISTENBRAINZ_USERNAME", "LISTENBRAINZ_API_TOKEN", "LIDARR_API_KEY"):
        monkeypatch.setattr(Config, name, None, raising=False)
    monkeypatch.setattr(Config, "DB_PATH", str(tmp_path / "main.db"), raising=False)
    monkeypatch.setattr("lyra.main.setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["lyra", "--fallback-only"])

    with caplog.at_level(logging.ERROR):
        assert main() == 1

    assert "Configuration error" in caplog.text
    assert "LIDARR_API_KEY" in caplog.text


def test_interactive_validation_does_not_require_the_batch_credentials(monkeypatch):
    """A manual download needs no ListenBrainz or Lidarr account."""
    for name in ("LISTENBRAINZ_USERNAME", "LISTENBRAINZ_API_TOKEN", "LIDARR_API_KEY"):
        monkeypatch.setattr(Config, name, None, raising=False)

    Config.validate_interactive()

    with pytest.raises(ValueError):
        Config.validate()


def test_integration_refuses_a_dry_run_with_a_wet_store(db):
    """The contradiction is refused at construction, as the orchestrator does.

    A dry integration recording through a wet store is the hole this task
    closes. Refusing the pair closes it for every route at once, instead of
    relying on the call site to remember it is dry.
    """
    with pytest.raises(ValueError) as excinfo:
        CLIDatabaseIntegration(state_manager=StateManager(db), dry_run=True)

    message = str(excinfo.value)
    assert "CLIDatabaseIntegration" in message
    assert "dry_run=True" in message
    assert "dry_run=False" in message
    assert "StateManager" in message  # the fix, not only the contradiction

    # The consistent pair is what the CLI builds, and it is accepted.
    integration = CLIDatabaseIntegration(
        state_manager=StateManager(db, dry_run=True), dry_run=True
    )
    assert integration.state_manager.dry_run is True


def test_interactive_validation_warns_about_a_missing_library_path(
    tmp_path, monkeypatch, caplog
):
    """It may not demand a library path, but it must say the path is missing."""
    monkeypatch.setattr(
        Config, "LIBRARY_PATH", str(tmp_path / "not-mounted"), raising=False
    )

    with caplog.at_level(logging.WARNING, logger="lyra.config"):
        Config.validate_interactive()

    assert "library path" in caplog.text.lower()
