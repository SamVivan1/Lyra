import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    LISTENBRAINZ_USERNAME = os.getenv("LISTENBRAINZ_USERNAME")
    LISTENBRAINZ_API_URL = "https://api.listenbrainz.org/1"
    LISTENBRAINZ_API_TOKEN = os.getenv("LISTENBRAINZ_API_TOKEN")
    
    LIDARR_URL = os.getenv("LIDARR_URL", "http://localhost:8686").rstrip("/")
    LIDARR_API_KEY = os.getenv("LIDARR_API_KEY")
    QUALITY_PROFILE_ID = int(os.getenv("QUALITY_PROFILE_ID", 1))
    METADATA_PROFILE_ID = int(os.getenv("METADATA_PROFILE_ID", 1))
    
    # Root path as seen BY Lidarr (API)
    LIDARR_ROOT_FOLDER_PATH = os.getenv("LIDARR_ROOT_FOLDER_PATH", "/music")
    
    MAX_ARTISTS_PER_RUN = int(os.getenv("MAX_ARTISTS_PER_RUN", 5))
    FALLBACK_HOURS = int(os.getenv("FALLBACK_HOURS", 24))
    
    # Paths as seen BY this script (Local Filesystem)
    LIBRARY_PATH = os.getenv("LIBRARY_PATH", "/mnt/Storage2/data/media/Music")
    DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", "/DATA/Downloads")
    DB_PATH = os.getenv("DB_PATH", "music_automation.db")
    
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    @classmethod
    def validate(cls):
        missing = []
        if not cls.LISTENBRAINZ_USERNAME:
            missing.append("LISTENBRAINZ_USERNAME")
        if not cls.LISTENBRAINZ_API_TOKEN:
            missing.append("LISTENBRAINZ_API_TOKEN")
        if not cls.LIDARR_API_KEY:
            missing.append("LIDARR_API_KEY")
            
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
