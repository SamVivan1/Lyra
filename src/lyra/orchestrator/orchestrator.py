"""
Main application orchestrator.

Coordinates artist addition, track tracking, and fallback downloads.
"""

import logging
from typing import Optional

from ..api import ListenBrainzClient, LidarrClient
from ..database.state import StateManager
from ..database.connection import DatabaseConnection
from ..database.track_manager import TrackStateManager
from ..downloader import FallbackOrchestrator
from ..config import Config

logger = logging.getLogger(__name__)


class ApplicationOrchestrator:
    """
    Main application orchestrator.
    
    Coordinates:
    - Fetching recommendations from ListenBrainz
    - Adding artists to Lidarr
    - Tracking recommended tracks for fallback
    - Running fallback downloads
    """

    def __init__(
        self,
        db_connection: Optional[DatabaseConnection] = None,
        state_manager: Optional[StateManager] = None
    ):
        """
        Initialize ApplicationOrchestrator.
        
        Args:
            db_connection: DatabaseConnection instance
            state_manager: StateManager instance
        """
        self.db = db_connection or DatabaseConnection()
        self.state_manager = state_manager or StateManager(self.db)
        
        self.lb_client = ListenBrainzClient()
        self.lidarr_client = LidarrClient()
        self.track_manager = TrackStateManager(self.db)
        self.fallback_orchestrator = FallbackOrchestrator(self.state_manager)

    def add_artists_from_recommendations(
        self,
        limit: int = Config.MAX_ARTISTS_PER_RUN,
        dry_run: bool = False
    ) -> int:
        """
        Add recommended artists to Lidarr and track their recommended tracks.
        
        Args:
            limit: Maximum number of artists to add per run
            dry_run: If True, don't perform actual operations
            
        Returns:
            Number of artists successfully added
        """
        logger.info("Starting artist addition from ListenBrainz recommendations...")
        
        try:
            Config.validate()
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            return 0

        recommendations = self.lb_client.get_recommended_artists_with_tracks()
        if not recommendations:
            logger.info("No recommendations found from ListenBrainz")
            return 0

        existing_artists = self.lidarr_client.get_existing_artists()
        existing_artists_lower = [a.lower() for a in existing_artists]
        tracked_artists = self.state_manager.get_tracked_artists()
        tracked_artists_lower = [a.lower() for a in tracked_artists]

        added_count = 0
        for artist, tracks in recommendations.items():
            if added_count >= limit:
                logger.info(f"Reached limit of {limit} artists per run")
                break

            if artist.lower() == "various artists":
                continue

            if artist.lower() in existing_artists_lower:
                logger.debug(f"Artist '{artist}' already in Lidarr")
            elif artist.lower() in tracked_artists_lower:
                logger.debug(f"Artist '{artist}' already tracked")
            else:
                logger.info(f"Adding new artist: {artist}")
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would add '{artist}' to Lidarr "
                        f"with {len(tracks)} tracks"
                    )
                    added_count += 1
                else:
                    success = self.lidarr_client.add_artist(artist)
                    if success:
                        self.state_manager.add_artist(artist, recommended_tracks=tracks)
                        added_count += 1

            # Track recommended tracks for fallback
            for track in tracks:
                if self.state_manager.track_exists(artist=artist, title=track):
                    logger.debug(
                        f"Track '{artist} - {track}' already exists locally"
                    )
                    continue
                
                self.track_manager.add_tracked_track(
                    None,
                    artist,
                    track,
                    source='listenbrainz'
                )

        logger.info(
            f"Finished: added {added_count} artists, "
            f"tracking recommended tracks"
        )
        return added_count

    def run_fallback(self, dry_run: bool = False) -> None:
        """
        Execute fallback download orchestration.
        
        Args:
            dry_run: If True, don't perform actual downloads
        """
        self.fallback_orchestrator.run_fallback(dry_run=dry_run)

    def ensure_library_indexed(self, force_rescan: bool = False) -> None:
        """
        Ensure music library is indexed in database.
        
        Args:
            force_rescan: Force complete rescan and reindex
        """
        self.state_manager.ensure_library_indexed(force_rescan=force_rescan)
