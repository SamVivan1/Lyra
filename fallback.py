from listenbrainz import ListenBrainzClient
from lidarr import LidarrClient
from downloader import Downloader
from state import StateManager
from config import Config
from utils import setup_logger

logger = setup_logger("fallback")

class FallbackOrchestrator:
    def __init__(self):
        self.lb_client = ListenBrainzClient()
        self.lidarr_client = LidarrClient()
        self.downloader = Downloader()
        self.state_manager = StateManager()

    def run_fallback(self):
        """Execute the fallback logic."""
        logger.info("Starting fallback check...")
        
        pending_artists = self.state_manager.get_pending_fallback_artists()
        if not pending_artists:
            logger.info("No artists pending fallback.")
            return

        missing_artists = self.lidarr_client.get_missing_artists()
        missing_artists_lower = [a.lower() for a in missing_artists]
        
        for artist in pending_artists:
            # Check if Lidarr actually resolved it (if it's no longer missing, maybe Lidarr downloaded it)
            # Lidarr missing check isn't foolproof if Lidarr removed it, but we assume it stays in wanted.
            # Another check is to just run fallback regardless, but checking missing ensures we don't duplicate.
            
            # Simple assumption: if the artist is missing albums, or we just want to fallback anyway.
            # Goal logic: "If content is not downloaded after 24 hours, trigger fallback".
            
            logger.info(f"Triggering fallback for '{artist}'")
            tracks = self.lb_client.get_top_tracks_for_artist(artist, limit=2)
            
            if not tracks:
                logger.warning(f"No tracks found for '{artist}' via ListenBrainz history. Skipping.")
                # We could mark as done to prevent infinite retries without data
                self.state_manager.mark_fallback_done(artist)
                continue
                
            success_count = 0
            for track in tracks:
                if self.downloader.download_track(artist, track):
                    success_count += 1
                    
            if success_count > 0:
                logger.info(f"Fallback successful for '{artist}' ({success_count} tracks).")
                self.state_manager.mark_fallback_done(artist)
            else:
                logger.warning(f"Fallback failed for '{artist}'. Will retry later.")

        logger.info("Fallback check completed.")
