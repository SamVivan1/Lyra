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

    def run_fallback(self, dry_run=False):
        """Execute the fallback logic."""
        logger.info("Starting fallback check...")
        
        pending_data = self.state_manager.get_pending_fallback_artists()
        if not pending_data:
            logger.info("No artists pending fallback.")
            return

        missing_artists = self.lidarr_client.get_missing_artists()
        missing_artists_lower = [a.lower() for a in missing_artists]
        
        for artist_info in pending_data:
            artist = artist_info["name"]
            recommended_tracks = artist_info["recommended_tracks"]
            
            # Check if Lidarr actually resolved it
            if artist.lower() not in missing_artists_lower:
                logger.info(f"Artist '{artist}' is no longer missing in Lidarr. Marking fallback as done.")
                if not dry_run:
                    self.state_manager.mark_fallback_done(artist)
                continue
            
            logger.info(f"Triggering fallback for '{artist}'")
            
            # 1. Try to find tracks from history
            tracks = self.lb_client.get_top_tracks_for_artist(artist, limit=2)
            
            # 2. If no history, use recommended tracks from DB
            if not tracks and recommended_tracks:
                logger.info(f"No history found for '{artist}', using {len(recommended_tracks)} recommended tracks.")
                tracks = recommended_tracks[:2] # Limit to 2 for now to avoid massive downloads
            
            if not tracks:
                logger.warning(f"No tracks found for '{artist}' (history or recommendations). Skipping for now.")
                continue
                
            success_count = 0
            for track in tracks:
                if self.downloader.download_track(artist, track, dry_run=dry_run):
                    success_count += 1
                    
            if success_count > 0:
                logger.info(f"Fallback successful for '{artist}' ({success_count} tracks).")
                if not dry_run:
                    self.state_manager.mark_fallback_done(artist)
            else:
                logger.warning(f"Fallback failed for '{artist}'. Will retry later.")

        logger.info("Fallback check completed.")
