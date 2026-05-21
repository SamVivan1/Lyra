"""
Main entry point for Lyra Music Automation.

Provides CLI argument parsing and orchestration entry point.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import Config
from .logging_config import setup_logger, configure_root_logger
from .database.connection import DatabaseConnection
from .database.library_scanner import LibraryScanner
from .orchestrator import ApplicationOrchestrator

logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO") -> None:
    """Setup logging for the application."""
    configure_root_logger(getattr(logging, log_level))
    setup_logger("lyra", level=getattr(logging, log_level))


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

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_level)
    logger.info("=" * 60)
    logger.info("Lyra Music Automation System - Starting")
    logger.info("=" * 60)

    try:
        # Validate configuration
        Config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    # Interactive mode
    if args.interactive:
        from .cli import InteractiveDownloader
        downloader = InteractiveDownloader()
        downloader.run()
        return 0

    # Initialize components
    db_connection = DatabaseConnection(Config.DB_PATH)
    orchestrator = ApplicationOrchestrator(db_connection)

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
