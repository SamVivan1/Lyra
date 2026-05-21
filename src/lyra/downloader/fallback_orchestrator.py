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

    def run_fallback(self, dry_run: bool = False) -> None:
        """
        Execute fallback download logic for pending tracks.
        
        Args:
            dry_run: If True, don't perform actual downloads
        """
        logger.info("Starting fallback check...")

        # Import TrackStateManager locally to avoid circular imports
        from ..database.track_manager import TrackStateManager
        from ..config import Config

        track_manager = TrackStateManager(self.state_manager.db)
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
                if not dry_run:
                    track_manager.mark_tracked_track_completed(track_id=track_id)
                continue

            logger.info(f"Triggering fallback download for '{artist} - {title}'")

            if dry_run:
                logger.info(f"[DRY RUN] Would download: '{artist} - {title}'")
                continue

            # Attempt download
            success = self.downloader.download_track(artist, title, dry_run=False)
            if success:
                # Add to local library
                self.state_manager.add_local_track(
                    artist,
                    title,
                    recording_mbid=recording_mbid,
                    added_at=self.state_manager._now_iso()
                )
                track_manager.mark_tracked_track_completed(track_id=track_id)
                logger.info(f"Fallback completed for '{artist} - {title}'")
            else:
                logger.warning(
                    f"Fallback download failed for '{artist} - {title}'. "
                    "Will retry later."
                )

        logger.info("Fallback check completed")
