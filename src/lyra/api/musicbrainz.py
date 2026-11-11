"""
MusicBrainz API client for metadata resolution.

Provides recording search and metadata extraction capabilities.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseAPIClient

logger = logging.getLogger(__name__)


class MusicBrainzClient(BaseAPIClient):
    """
    MusicBrainz API client for metadata resolution.
    
    Searches recordings and extracts comprehensive release metadata.
    """

    def __init__(
        self,
        base_url: str = "https://musicbrainz.org/ws/2",
        rate_limit_delay: float = 1.0
    ):
        """
        Initialize MusicBrainzClient.
        
        Args:
            base_url: MusicBrainz API base URL
            rate_limit_delay: Rate limit delay (MusicBrainz requires slow requests)
        """
        headers = {
            "Accept": "application/json",
            "User-Agent": "Lyra/1.0 (Music Automation)"
        }
        super().__init__(base_url, headers=headers, rate_limit_delay=rate_limit_delay)

    def search_recording(
        self,
        artist: str,
        title: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search for recordings by artist and title.
        
        Args:
            artist: Artist name
            title: Recording title
            limit: Maximum results
            
        Returns:
            List of recording objects from MusicBrainz
        """
        title_variants = self._normalize_search_terms(title)
        artist_variants = self._normalize_search_terms(artist)
        queries = []

        for title_variant in title_variants:
            for artist_variant in artist_variants:
                if title_variant and artist_variant:
                    queries.append(
                        f'recording:"{self._escape_query(title_variant)}" '
                        f'AND artist:"{self._escape_query(artist_variant)}"'
                    )
        
        for title_variant in title_variants:
            if title_variant:
                queries.append(f'recording:"{self._escape_query(title_variant)}"')
                queries.append(f'"{self._escape_query(title_variant)}"')
        
        for artist_variant in artist_variants:
            if artist_variant:
                queries.append(f'artist:"{self._escape_query(artist_variant)}"')
                queries.append(f'"{self._escape_query(artist_variant)}"')

        for query in queries:
            params = {
                "fmt": "json",
                "query": query,
                "limit": limit,
                "inc": "artist-credits+releases"
            }
            try:
                response = self._get("/recording", params=params)
                if response and response.get("recordings"):
                    return response["recordings"]
            except Exception as e:
                logger.debug(f"MusicBrainz search failed for query '{query}': {e}")
                continue

        return []

    def get_best_release_metadata(
        self,
        artist: str,
        title: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get best release metadata for an artist/title combination.
        
        Args:
            artist: Artist name
            title: Track title
            
        Returns:
            Dictionary with album, year, release_date, etc. or None
        """
        recordings = self.search_recording(artist, title, limit=5)
        if not recordings:
            return None

        match = self._select_best_recording(artist, title, recordings)
        if not match:
            match = recordings[0]

        return self._extract_release_metadata(match)

    @staticmethod
    def _normalize_search_terms(text: Optional[str]) -> List[str]:
        """Normalize search terms by removing parentheticals."""
        if not text:
            return []
        
        cleaned = text.strip()
        stripped = MusicBrainzClient._strip_parenthetical(cleaned).strip()
        results = [cleaned]
        if stripped and stripped != cleaned:
            results.append(stripped)
        return list(dict.fromkeys(results))

    @staticmethod
    def _strip_parenthetical(text: str) -> str:
        """Remove parenthetical content from text."""
        result = text
        while '(' in result and ')' in result:
            start = result.index('(')
            end = result.index(')', start)
            result = (result[:start] + result[end + 1:]).strip()
        return result

    @staticmethod
    def _escape_query(text: Optional[str]) -> str:
        """Escape special characters in search query."""
        if not text:
            return ""
        return text.replace('"', '').strip()

    @staticmethod
    def _normalize(text: Optional[str]) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        return text.lower().strip()

    def _select_best_recording(
        self,
        artist: str,
        title: str,
        recordings: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Select best matching recording from list."""
        title_norm = self._normalize(title)
        artist_norm = self._normalize(artist)

        for recording in recordings:
            if self._normalize(recording.get("title")) != title_norm:
                continue

            credits = recording.get("artist-credit", [])
            if any(
                self._normalize(self._artist_credit_name(ac)) == artist_norm
                for ac in credits
            ):
                return recording

        return None

    @staticmethod
    def _artist_credit_name(credit: Any) -> str:
        """Extract artist name from credit structure."""
        if isinstance(credit, dict):
            artist = credit.get("artist", {})
            return artist.get("name") or credit.get("name") or ""
        return str(credit or "")

    @staticmethod
    def _join_artist_credit(credits: Optional[List[Any]]) -> Optional[str]:
        """Join artist credits into single string."""
        if not credits:
            return None

        names = []
        for credit in credits:
            name = MusicBrainzClient._artist_credit_name(credit)
            if name:
                names.append(name)

        return ", ".join(names) if names else None

    @staticmethod
    def _extract_release_metadata(
        recording: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract metadata from recording object."""
        metadata = {
            "recording_mbid": recording.get("id"),
            "recording_title": recording.get("title"),
            "album": None,
            "album_artist": None,
            "year": None,
            "release_date": None,
            "track_position": None
        }

        releases = recording.get("releases", [])
        if not releases:
            return metadata

        releases = sorted(
            releases,
            key=lambda r: r.get("date") or "9999-99-99"
        )
        release = releases[0]

        metadata["album"] = release.get("title")
        release_date = release.get("date")
        metadata["release_date"] = release_date
        metadata["year"] = release_date.split("-")[0] if release_date else None
        metadata["album_artist"] = MusicBrainzClient._join_artist_credit(
            release.get("artist-credit") or recording.get("artist-credit", [])
        )

        for medium in release.get("media", []):
            for track in medium.get("track-list", []):
                recording_ref = track.get("recording", {})
                if recording_ref.get("id") == recording.get("id"):
                    metadata["track_position"] = track.get("position")
                    return metadata

        return metadata
