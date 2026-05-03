import subprocess
import os
import glob
from config import Config
from utils import setup_logger

logger = setup_logger("downloader")

class Downloader:
    def __init__(self, library_path=Config.LIBRARY_PATH):
        self.library_path = library_path

    def _track_exists(self, artist, track):
        """Simple deduplication check: look for similar filenames in the library."""
        # Sanitize for globbing
        safe_artist = artist.replace('*', '').replace('?', '')
        safe_track = track.replace('*', '').replace('?', '')
        
        pattern = os.path.join(self.library_path, f"*{safe_artist}*{safe_track}*")
        matches = glob.glob(pattern)
        return len(matches) > 0

    def download_track(self, artist, track):
        """Download track from YouTube using yt-dlp."""
        if self._track_exists(artist, track):
            logger.info(f"Track '{artist} - {track}' already exists in library. Skipping download.")
            return True

        query = f"ytsearch1:{artist} {track} official audio"
        output_template = os.path.join(self.library_path, "%(artist)s - %(title)s.%(ext)s")
        
        command = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--output", output_template,
            query
        ]
        
        logger.info(f"Downloading fallback track: '{artist} - {track}'")
        
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            logger.debug(f"yt-dlp output: {result.stdout}")
            logger.info(f"Successfully downloaded '{artist} - {track}'")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"yt-dlp failed for '{artist} - {track}'. Return code: {e.returncode}")
            logger.error(f"Error output: {e.stderr}")
            return False
        except FileNotFoundError:
            logger.error("yt-dlp not found! Please ensure it is installed and in your PATH.")
            return False
