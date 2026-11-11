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
        state_manager: Optional[StateManager] = None,
        dry_run: bool = False
    ):
        """
        Initialize ApplicationOrchestrator.

        The dry-run flag is handed to every store this orchestrator writes
        through, so the decision is made once here instead of at each call site.

        Args:
            db_connection: DatabaseConnection instance
            state_manager: StateManager instance
            dry_run: If True, no row is written and no artist is posted to Lidarr
        """
        self.db = db_connection or DatabaseConnection()
        self.dry_run = dry_run or (getattr(state_manager, "dry_run", False) is True)
        self.state_manager = state_manager or StateManager(self.db, dry_run=self.dry_run)

        self.lb_client = ListenBrainzClient()
        self.lidarr_client = LidarrClient()
        self.track_manager = TrackStateManager(self.db, dry_run=self.dry_run)
        self.fallback_orchestrator = FallbackOrchestrator(self.state_manager)

    def _add_artist_to_lidarr(self, artist: str, dry_run: bool) -> bool:
        """
        Add one artist to Lidarr, or report the add a dry run would make.

        A dry run must not reach Lidarr: adding an artist is a POST to a system
        outside this one, and no later step can undo it.

        Args:
            artist: Artist name
            dry_run: If True, log the intended add and stop

        Returns:
            True if the artist was added, or would have been
        """
        if dry_run:
            logger.info(f"[DRY RUN] would add '{artist}' to Lidarr")
            return True
        return bool(self.lidarr_client.add_artist(artist))

    def add_artists_from_recommendations(
        self,
        limit: int = Config.MAX_ARTISTS_PER_RUN,
        dry_run: bool = False
    ) -> int:
        """
        Add recommended artists to Lidarr and track their recommended tracks.

        One code path serves both modes. The write-capable objects decide what
        happens to a write, so the only thing the dry run changes here is the
        POST to Lidarr, which no store can intercept.

        Args:
            limit: Maximum number of artists to add per run
            dry_run: If True, don't perform actual operations (or-ed with the
                orchestrator's own flag)
            
        Returns:
            Number of artists successfully added
        """
        dry_run = dry_run or self.dry_run

        # The flag belongs to the store that writes, so a call-level flag has to
        # reach one before the first write. The orchestrator's own managers are
        # reused when they already agree, so an injected manager stays in charge.
        state_writes = self.state_manager
        track_writes = self.track_manager
        if dry_run and getattr(state_writes, "dry_run", False) is not True:
            state_writes = StateManager(self.db, dry_run=True)
        if dry_run and getattr(track_writes, "dry_run", False) is not True:
            track_writes = TrackStateManager(self.db, dry_run=True)

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
                if self._add_artist_to_lidarr(artist, dry_run):
                    state_writes.add_artist(artist, recommended_tracks=tracks)
                    added_count += 1

            # Track recommended tracks for fallback
            for track in tracks:
                if self.state_manager.track_exists(artist=artist, title=track):
                    logger.debug(
                        f"Track '{artist} - {track}' already exists locally"
                    )
                    continue

                track_writes.add_tracked_track(
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
        self.fallback_orchestrator.run_fallback(dry_run=dry_run or self.dry_run)

    def ensure_library_indexed(self, force_rescan: bool = False) -> None:
        """
        Ensure music library is indexed in database.
        
        Args:
            force_rescan: Force complete rescan and reindex
        """
        self.state_manager.ensure_library_indexed(force_rescan=force_rescan)
