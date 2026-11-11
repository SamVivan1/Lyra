"""
Main entry point for Lyra Music Automation.

Provides CLI argument parsing and orchestration entry point.
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

from .config import Config
from .logging_config import setup_logger, configure_root_logger
from .database.connection import DatabaseConnection
from .database.errors import DatabaseCorruptError, DatabaseFileError
from .database.library_scanner import LibraryScanner
from .database.maintenance import repair_database
from .orchestrator import ApplicationOrchestrator

# Named explicitly rather than with ``__name__``: ``python -m lyra.main`` runs
# this module as ``__main__``, whose logger has no handler, so the operator would
# see none of the messages below -- including the repair summary.
logger = logging.getLogger("lyra.main")


def setup_logging(log_level: str = "INFO") -> None:
    """Setup logging for the application."""
    configure_root_logger(getattr(logging, log_level))
    setup_logger("lyra", level=getattr(logging, log_level))


def repair_database_mode(db_path: str) -> int:
    """
    Back up and rebuild the database file, returning the process exit code.

    Kept separate from :func:`main` so the repair path can be exercised without
    a configured environment, and so ``--repair-db`` provably returns before any
    other mode runs.

    Args:
        db_path: Path of the database file to repair.

    Returns:
        0 when the rebuilt database passes its integrity check, 1 otherwise.
    """
    try:
        report = repair_database(Path(db_path))
    except (DatabaseCorruptError, DatabaseFileError) as e:
        logger.error(str(e))
        return 1

    logger.info(f"Old database backed up to {report.backup_path}")
    if not report.integrity_ok:
        logger.error(f"Rebuilt database at {db_path} failed its integrity check")
        return 1
    for table, rows in report.tables.items():
        logger.info(f"The old database had {rows} rows in {table}")
    if not report.tables:
        logger.warning(
            f"Could not count the rows in the old database at {db_path}; "
            f"the backup at {report.backup_path} is the only copy of them"
        )
    logger.info("Database repair finished")
    return 0


def _startup_error_message(error: Exception, db_path: str) -> str:
    """
    Turn a failure to open the database at startup into one actionable line.

    ``DatabaseCorruptError`` and ``DatabaseFileError`` already name the file and
    say what to do with it, so their message is used unchanged (the corrupt one
    names ``lyra --repair-db``). A bare ``sqlite3`` error carries no advice: a
    locked or unopenable database is a concurrency/path condition, not evidence
    of corruption, so it must not be answered with the repair command.

    Args:
        error: The exception that escaped ``initialize()``.
        db_path: Database path startup was using, for the message.

    Returns:
        A single log line for the operator, without a traceback.
    """
    if isinstance(error, (DatabaseCorruptError, DatabaseFileError)):
        return str(error)
    return (
        f"Could not open the database at {db_path}: {error}. "
        "Check that the path is a writable file that no other process is using."
    )


def _open_initialised_database(db_path: str) -> Optional[DatabaseConnection]:
    """
    Open and initialise the database, reporting a failure instead of raising.

    Every mode meets the same failures here -- a corrupt file, an unwritable
    path, a database another process is holding -- and the operator needs the
    same actionable line for each, so the handling lives in one place.

    Args:
        db_path: Path of the database file.

    Returns:
        The initialised connection, or None when startup must stop.
    """
    connection = DatabaseConnection(db_path)
    try:
        connection.initialize()
    except (DatabaseCorruptError, DatabaseFileError, sqlite3.Error) as e:
        # Without this the operator meets a raw traceback instead of the advice
        # these errors were written to carry -- the `lyra --repair-db` hint, the
        # unwritable path, or the busy database. Reporting only: never repair.
        logger.error(_startup_error_message(e, db_path))
        return None
    return connection


def run_interactive_mode(dry_run: bool = False) -> int:
    """
    Run the interactive manual-download CLI, returning the process exit code.

    The database is initialised here, before the downloader is built: the
    integration it holds is uninitialised until then, so on a fresh database the
    first record would otherwise fail with ``no such table``. The initialised
    connection is handed to that integration rather than to a second one.

    Args:
        dry_run: If True, nothing is downloaded, tagged or recorded.

    Returns:
        0 on a clean exit, 1 when the database cannot be opened.
    """
    from .cli import InteractiveDownloader
    from .cli.db_integration import CLIDatabaseIntegration

    # Only what a manual download needs: this mode talks to neither
    # ListenBrainz nor Lidarr, so the batch credentials are not asked for.
    Config.validate_interactive()

    db_connection = _open_initialised_database(Config.DB_PATH)
    if db_connection is None:
        return 1

    downloader = InteractiveDownloader(
        db_integration=CLIDatabaseIntegration(
            db_connection=db_connection, dry_run=dry_run
        ),
        dry_run=dry_run,
    )
    downloader.run()
    return 0


def main() -> int:
    """
    Main entry point for CLI.
    
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description="Self-Hosted Music Automation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Normal operation (fetch recommendations and fallback)
  python -m lyra.main
  
  # Force rescan music library and then run normally
  python -m lyra.main --rescan
  
  # Only add artists from ListenBrainz
  python -m lyra.main --add-only
  
  # Only run fallback downloads
  python -m lyra.main --fallback-only
  
  # Dry run (simulate without actual changes)
  python -m lyra.main --dry-run
  
  # Run continuously every 6 hours
  python -m lyra.main --loop

  # Repair a corrupt database (backs it up, then rebuilds it empty)
  python -m lyra.main --repair-db
        """
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate operations without making actual changes"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=Config.MAX_ARTISTS_PER_RUN,
        help=f"Limit artists per run (default: {Config.MAX_ARTISTS_PER_RUN})"
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help="Force filesystem rescan and refresh local cache"
    )
    parser.add_argument(
        "--add-only",
        action="store_true",
        help="Only add artists from ListenBrainz to Lidarr"
    )
    parser.add_argument(
        "--fallback-only",
        action="store_true",
        help="Only run the fallback (YouTube) download logic"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously in a loop"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=21600,
        help="Loop interval in seconds (default: 6 hours)"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Run interactive downloader CLI"
    )
    parser.add_argument(
        "--repair-db",
        action="store_true",
        help="Back up the database file and rebuild it empty, then exit"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_level)
    logger.info("=" * 60)
    logger.info("Lyra Music Automation System - Starting")
    logger.info("=" * 60)

    # Explicit repair runs before any other mode. It has to: the file it repairs
    # may be one that normal startup cannot open at all.
    if args.repair_db:
        return repair_database_mode(Config.DB_PATH)

    # Interactive mode comes first and validates only itself: it is the one mode
    # a manual download needs, and it needs no ListenBrainz or Lidarr account --
    # so the batch credentials must not stand between the operator and it.
    if args.interactive:
        return run_interactive_mode(dry_run=args.dry_run)

    try:
        # Validate configuration for the batch modes
        Config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    # Initialize components
    db_connection = _open_initialised_database(Config.DB_PATH)
    if db_connection is None:
        return 1
    # The dry-run flag is a property of the stores the orchestrator builds, so
    # it is handed over once here: every write path below then inherits it.
    orchestrator = ApplicationOrchestrator(db_connection, dry_run=args.dry_run)

    # Ensure library is indexed
    logger.info("Ensuring music library is indexed...")
    if args.rescan:
        logger.info("Forcing library rescan...")
    orchestrator.ensure_library_indexed(force_rescan=args.rescan)

    # Define job function
    def run_job() -> None:
        """Run orchestration job."""
        if args.fallback_only:
            orchestrator.run_fallback(dry_run=args.dry_run)
        elif args.add_only:
            orchestrator.add_artists_from_recommendations(
                limit=args.limit,
                dry_run=args.dry_run
            )
        else:
            orchestrator.add_artists_from_recommendations(
                limit=args.limit,
                dry_run=args.dry_run
            )
            orchestrator.run_fallback(dry_run=args.dry_run)

    # Loop mode
    if args.loop:
        logger.info(f"Running in loop mode, interval: {args.interval} seconds")
        while True:
            try:
                run_job()
            except Exception as e:
                logger.error(f"Error in job execution: {e}", exc_info=True)
            
            logger.info(f"Next run in {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        # One-shot mode
        logger.info("Running in one-shot mode")
        try:
            run_job()
        except Exception as e:
            logger.error(f"Error in job execution: {e}", exc_info=True)
            return 1

    logger.info("Lyra Music Automation - Finished")
    return 0


def run() -> None:
    """Run the Lyra CLI entry point and exit with the returned code."""
    sys.exit(main())


if __name__ == "__main__":
    run()
