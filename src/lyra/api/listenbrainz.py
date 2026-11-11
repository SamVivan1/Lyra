"""
ListenBrainz API client with full recording_mbid support.

Provides methods to fetch recommendations and resolve metadata.
"""

import logging
from typing import Dict, List, Optional

from ..config import Config
from .base import BaseAPIClient

logger = logging.getLogger(__name__)


class ListenBrainzClient(BaseAPIClient):
    """
    ListenBrainz API client for fetching music recommendations.
    
    Fetches recommendation data with recording MBIDs and resolves
    artist/track information via MusicBrainz.
    """

    def __init__(
        self,
        username: Optional[str] = None,
        token: Optional[str] = None,
        base_url: str = Config.LISTENBRAINZ_API_URL,
        rate_limit_delay: float = Config.API_RATE_LIMIT_DELAY
    ):
        """
        Initialize ListenBrainzClient.
        
        Args:
            username: ListenBrainz username
            token: ListenBrainz API token
            base_url: API base URL
            rate_limit_delay: Rate limit delay in seconds
        """
        self.username = username or Config.LISTENBRAINZ_USERNAME
        self.token = token or Config.LISTENBRAINZ_API_TOKEN
        
        headers = {
            "Accept": "application/json",
            "User-Agent": "Lyra/1.0 (Music Automation)"
        }
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        
        super().__init__(base_url, headers=headers, rate_limit_delay=rate_limit_delay)

    def get_recommended_recording_mbids(
        self,
        count: int = 10,
        offset: int = 0
    ) -> List[str]:
        """
        Fetch recommended recording MBIDs from ListenBrainz.
        
        Args:
            count: Number of recommendations to fetch
            offset: Offset for pagination
            
        Returns:
            List of recording MBIDs
        """
        if not self.username:
            logger.error("ListenBrainz username not configured")
            return []

        url = f"/cf/recommendation/user/{self.username}/recording"
        params = {"count": count, "offset": offset}
        
        logger.debug(f"Fetching {count} recording recommendations from ListenBrainz")
        
        try:
            response = self._get(url, params=params)
            if response is None:
                return []
            
            if not response.get("payload"):
                logger.info("ListenBrainz returned empty payload")
                return []
            
            mbids = [
                item.get("recording_mbid")
                for item in response["payload"].get("mbids", [])
                if item.get("recording_mbid")
            ]
            logger.debug(f"Fetched {len(mbids)} recording MBIDs")
            return mbids
        except Exception as e:
            logger.error(f"Error fetching recommendations: {e}")
            return []

    def get_recording_metadata(
        self,
        recording_mbid: str
    ) -> tuple[List[str], Optional[str]]:
        """
        Resolve recording MBID to artist and track title via MusicBrainz.
        
        Args:
            recording_mbid: MusicBrainz recording ID
            
        Returns:
            Tuple of (artist_names_list, track_title)
        """
        url = f"https://musicbrainz.org/ws/2/recording/{recording_mbid}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Lyra/1.0 (Music Automation)"
        }
        params = {"fmt": "json", "inc": "artist-credits"}
        
        try:
            import requests
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            title = data.get("title")
            artist_names = []
            
            for ac in data.get("artist-credit", []):
                if isinstance(ac, dict):
                    artist = ac.get("artist", {})
                    name = artist.get("name") or ac.get("name")
                    if name:
                        artist_names.append(name)
                elif isinstance(ac, str):
                    artist_names.append(ac)
            
            return artist_names, title
        except Exception as e:
            logger.warning(f"Failed to resolve recording {recording_mbid}: {e}")
            return [], None

    def get_recommended_artists_with_tracks(self) -> Dict[str, List[str]]:
        """
        Fetch recommended artists and their tracks from ListenBrainz.
        
        Returns:
            Dictionary mapping artist name to list of track titles
        """
        recording_mbids = self.get_recommended_recording_mbids()
        if not recording_mbids:
            logger.info("No recommendations found")
            return {}

        recommendations: Dict[str, set] = {}
        
        for mbid in recording_mbids:
            artist_names, track_title = self.get_recording_metadata(mbid)
            if not track_title:
                continue
            
            for artist_name in artist_names:
                if artist_name not in recommendations:
                    recommendations[artist_name] = set()
                recommendations[artist_name].add(track_title)

        return {k: list(v) for k, v in recommendations.items()}

    def get_recommended_artists(self) -> List[str]:
        """
        Get list of recommended artist names.
        
        Returns:
            List of artist names
        """
        return list(self.get_recommended_artists_with_tracks().keys())

    def get_top_tracks_for_artist(
        self,
        artist_name: str,
        limit: int = 5
    ) -> List[str]:
        """
        Fetch user's top tracks for a specific artist from listening history.
        
        Args:
            artist_name: Artist name
            limit: Maximum number of tracks to return
            
        Returns:
            List of track titles
        """
        if not self.username:
            logger.error("ListenBrainz username not configured")
            return []

        url = f"/user/{self.username}/listens"
        params = {"count": 100}
        
        logger.debug(f"Fetching listening history for artist '{artist_name}'")
        
        try:
            response = self._get(url, params=params)
            if not response:
                return []
            
            tracks = set()
            listens = response.get("payload", {}).get("listens", [])
            
            for listen in listens:
                track_meta = listen.get("track_metadata", {})
                current_artist = track_meta.get("artist_name")
                
                if current_artist and current_artist.lower() == artist_name.lower():
                    track_name = track_meta.get("track_name")
                    if track_name:
                        tracks.add(track_name)
                        if len(tracks) >= limit:
                            break
            
            return list(tracks)
        except Exception as e:
            logger.error(f"Error fetching listening history: {e}")
            return []
