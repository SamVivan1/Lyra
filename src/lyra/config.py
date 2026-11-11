"""
Configuration management for Lyra Music Automation.

Loads configuration from environment variables and optionally from a local .env file.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from the current working directory when the package is imported.
load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)

logger = logging.getLogger(__name__)


class Config:
    """Application configuration loaded from environment variables."""

    # ListenBrainz Configuration
    LISTENBRAINZ_USERNAME: Optional[str] = os.getenv("LISTENBRAINZ_USERNAME")
    LISTENBRAINZ_API_URL: str = os.getenv("LISTENBRAINZ_API_URL", "https://api.listenbrainz.org/1")
    LISTENBRAINZ_API_TOKEN: Optional[str] = os.getenv("LISTENBRAINZ_API_TOKEN")

    # Lidarr Configuration
    LIDARR_URL: str = os.getenv("LIDARR_URL", "http://localhost:8686").rstrip("/")
    LIDARR_API_KEY: Optional[str] = os.getenv("LIDARR_API_KEY")
    QUALITY_PROFILE_ID: int = int(os.getenv("QUALITY_PROFILE_ID", "1"))
    METADATA_PROFILE_ID: int = int(os.getenv("METADATA_PROFILE_ID", "1"))
    LIDARR_ROOT_FOLDER_PATH: str = os.getenv("LIDARR_ROOT_FOLDER_PATH", "/music")

    # Paths Configuration
    LIBRARY_PATH: str = os.getenv(
        "LIBRARY_PATH",
        "/mnt/Storage2/data/media/Music"
    )
    DOWNLOAD_PATH: str = os.getenv("DOWNLOAD_PATH", "/DATA/Downloads")
    DB_PATH: str = os.getenv("DB_PATH", "music_automation.db")

    @classmethod
    def parse_library_paths(cls, raw_paths: str) -> list[str]:
        """
        Parse one or more library paths from a single environment variable.

        Supports comma, pipe, semicolon, OS path separator, and newline separators.
        """
        if not raw_paths:
            return []

        separators = {",", "|", ";", "\n", os.pathsep}
        normalized = raw_paths
        for sep in separators:
            if sep != ",":
                normalized = normalized.replace(sep, ",")

        paths = [path.strip() for path in normalized.split(",") if path.strip()]
        # dict.fromkeys keeps the configured order while dropping repeated roots:
        # a copy-pasted LIBRARY_PATH otherwise scans and logs the same tree twice.
        return list(dict.fromkeys(paths))

    @classmethod
    def library_paths(cls) -> list[str]:
        """Return configured library paths as a list of absolute paths."""
        return cls.parse_library_paths(cls.LIBRARY_PATH)

    # Application Configuration
    MAX_ARTISTS_PER_RUN: int = int(os.getenv("MAX_ARTISTS_PER_RUN", "5"))
    FALLBACK_HOURS: int = int(os.getenv("FALLBACK_HOURS", "24"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # API Rate Limiting
    API_RATE_LIMIT_DELAY: float = float(os.getenv("API_RATE_LIMIT_DELAY", "2"))

    @classmethod
    def validate(cls) -> None:
        """
        Validate required configuration is present.
        
        Raises:
            ValueError: If required environment variables are missing
        """
        missing = []
        
        if not cls.LISTENBRAINZ_USERNAME:
            missing.append("LISTENBRAINZ_USERNAME")
        if not cls.LISTENBRAINZ_API_TOKEN:
            missing.append("LISTENBRAINZ_API_TOKEN")
        if not cls.LIDARR_API_KEY:
            missing.append("LIDARR_API_KEY")
        
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

    @classmethod
    def validate_interactive(cls) -> None:
        """
        Validate only what the interactive manual-download mode needs.

        A manual download talks to neither ListenBrainz nor Lidarr, so requiring
        their credentials would lock the operator out of the one mode that needs
        no account. Nothing here is fatal: a library path that does not exist
        yet is worth saying out loud, because the downloader falls back to
        ``DOWNLOAD_PATH`` and the file would land somewhere unexpected.

        Unlike :meth:`validate`, this never raises.
        """
        if not any(os.path.exists(path) for path in cls.library_paths()):
            logger.warning(
                f"No configured library path exists ({cls.LIBRARY_PATH}); manual "
                f"downloads will be written to {cls.DOWNLOAD_PATH} until one does"
            )

    @classmethod
    def get_env_file_path(cls) -> Path:
        """Get path to .env file for development."""
        return Path.cwd() / ".env"
