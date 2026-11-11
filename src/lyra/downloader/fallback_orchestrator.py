"""
Fallback orchestrator for YouTube downloads.

Manages the fallback process when tracks are not found in Lidarr after 24 hours.
"""

import logging
from typing import Optional

from .downloader import Downloader
from ..database.state import StateManager
from ..database.connection import DatabaseConnection

logger = logging.getLogger(__name__)


class FallbackOrchestrator:
    """
    Orchestrates fallback YouTube downloads for missing tracks.
    
    Checks pending tracks after 24 hours and downloads via YouTube if not found locally.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        downloader: Optional[Downloader] = None,
        db_connection: Optional[DatabaseConnection] = None
    ):
        """
        Initialize FallbackOrchestrator.
        
        Args:
            state_manager: StateManager instance (creates new if None)
            downloader: Downloader instance (creates new if None)
            db_connection: DatabaseConnection instance
        """
        self.state_manager = state_manager or StateManager(db_connection)
        self.downloader = downloader or Downloader()

    @property
    def _store_dry_run(self) -> bool:
        """
        Whether the objects this orchestrator writes through are in dry-run mode.

        The flag belongs to the store, so this only reports what those objects
        already decided; the constructor has no flag of its own. ``is True`` and
        not a plain truthiness test: a test double answers any attribute with a
        truthy mock, and that must not turn a real run into a dry one.
        """
        return (
            getattr(self.state_manager, "dry_run", False) is True
            or getattr(self.downloader, "dry_run", False) is True
        )

    def run_fallback(self, dry_run: bool = False) -> None:
        """
        Execute fallback download logic for pending tracks.
        
        Args:
            dry_run: If True, don't perform actual downloads
        """
        dry_run = dry_run or self._store_dry_run

        logger.info("Starting fallback check...")

        # Import TrackStateManager locally to avoid circular imports
        from ..database.track_manager import TrackStateManager
        from ..config import Config

        # The flag travels with the store: this manager is built for the run,
        # and so is a dry state manager if the injected one is not one already.
        track_manager = TrackStateManager(self.state_manager.db, dry_run=dry_run)
        state_writes = self.state_manager
        if dry_run and getattr(state_writes, "dry_run", False) is not True:
            state_writes = StateManager(self.state_manager.db, dry_run=True)
        pending_tracks = track_manager.get_pending_tracked_tracks(hours=Config.FALLBACK_HOURS)
        if not pending_tracks:
            logger.info("No pending tracks eligible for fallback")
            return

        logger.info(f"Found {len(pending_tracks)} pending tracks for fallback")

        for track_info in pending_tracks:
            track_id = track_info["id"]
            artist = track_info["artist"]
            title = track_info["title"]
            recording_mbid = track_info.get("recording_mbid")

            # Check if track exists locally
            if self.state_manager.track_exists(
                artist=artist,
                title=title,
                recording_mbid=recording_mbid
            ):
                logger.info(
                    f"Track '{artist} - {title}' is available locally. "
                    "Marking as completed."
                )
                track_manager.mark_tracked_track_completed(track_id=track_id)
                continue

            logger.info(f"Triggering fallback download for '{artist} - {title}'")

            # One path for both modes: the dry-run flag reaches the downloader,
            # whose own store decides whether the file is written, and the
            # manager above decides whether the row is completed.
            success = self.downloader.download_track(artist, title, dry_run=dry_run)
            if success:
                # Add to local library
                state_writes.add_local_track(
                    artist,
                    title,
                    recording_mbid=recording_mbid,
                    added_at=state_writes._now_iso()
                )
                track_manager.mark_tracked_track_completed(track_id=track_id)
                logger.info(
                    f"{'[DRY RUN] Would have completed' if dry_run else 'Fallback completed'}"
                    f" for '{artist} - {title}'"
                )
            else:
                logger.warning(
                    f"Fallback download failed for '{artist} - {title}'. "
                    "Will retry later."
                )

        logger.info("Fallback check completed")
