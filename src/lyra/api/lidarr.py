"""
Lidarr API client for artist management.

Provides artist search, addition, and monitoring capabilities.
"""

import logging
from typing import Dict, List, Optional

from ..config import Config
from .base import BaseAPIClient

logger = logging.getLogger(__name__)


class LidarrClient(BaseAPIClient):
    """
    Lidarr API client for artist management.
    
    Handles artist search, addition to library, and missing content monitoring.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        rate_limit_delay: float = Config.API_RATE_LIMIT_DELAY
    ):
        """
        Initialize LidarrClient.
        
        Args:
            url: Lidarr base URL
            api_key: Lidarr API key
            rate_limit_delay: Rate limit delay in seconds
        """
        self.url = (url or Config.LIDARR_URL).rstrip("/")
        self.api_key = api_key or Config.LIDARR_API_KEY
        
        headers: Dict[str, str] = {
            # An unset key becomes an empty header rather than None, which
            # requests rejects outright; Config.validate() refuses to start
            # without a key, so this only affects clients built by hand.
            "X-Api-Key": self.api_key or "",
            "Accept": "application/json"
        }
        
        super().__init__(self.url, headers=headers, rate_limit_delay=rate_limit_delay)

    def get_existing_artists(self) -> List[str]:
        """
        Retrieve all artists currently in Lidarr.
        
        Returns:
            List of artist names
        """
        logger.info("Fetching existing artists from Lidarr")
        data = self._get("/api/v1/artist")
        
        if data is None:
            return []
        
        if not isinstance(data, list):
            logger.warning("Unexpected response format from Lidarr")
            return []
        
        return [artist.get("artistName", "") for artist in data if artist.get("artistName")]

    def add_artist(self, artist_name: str) -> bool:
        """
        Add a new artist to Lidarr.
        
        Args:
            artist_name: Name of artist to add
            
        Returns:
            True if successfully added, False otherwise
        """
        logger.info(f"Searching Lidarr for artist: {artist_name}")
        search_results = self._get("/api/v1/artist/lookup", params={"term": artist_name})
        
        if not search_results or not isinstance(search_results, list):
            logger.warning(f"Artist '{artist_name}' not found in Lidarr search")
            return False
        
        best_match = search_results[0]
        
        payload = best_match.copy()
        payload.update({
            "qualityProfileId": Config.QUALITY_PROFILE_ID,
            "metadataProfileId": Config.METADATA_PROFILE_ID,
            "rootFolderPath": Config.LIDARR_ROOT_FOLDER_PATH,
            "monitored": True,
            "addOptions": {
                "searchForMissingAlbums": True,
                "monitor": "all"
            }
        })
        
        logger.info(f"Adding artist '{best_match.get('artistName')}' to Lidarr")
        result = self._post("/api/v1/artist", payload)
        
        if result:
            logger.info(f"Successfully added artist '{best_match.get('artistName')}'")
            return True
        return False

    def get_missing_artists(self) -> List[str]:
        """
        Get artists with missing content in Lidarr.
        
        Returns:
            List of artist names with missing albums/tracks
        """
        logger.info("Fetching missing content from Lidarr")
        data = self._get("/api/v1/wanted/missing", params={"pageSize": 1000})
        
        if data is None:
            return []
        
        records = data.get("records", [])
        missing_artists = set()
        
        for record in records:
            artist = record.get("artist", {})
            artist_name = artist.get("artistName")
            if artist_name:
                missing_artists.add(artist_name)
        
        return list(missing_artists)
