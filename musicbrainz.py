import requests
from utils import setup_logger, rate_limit

logger = setup_logger("musicbrainz")

class MusicBrainzClient:
    def __init__(self, user_agent=None):
        self.base_url = "https://musicbrainz.org/ws/2"
        self.headers = {
            "Accept": "application/json",
            "User-Agent": user_agent or "MusicAutomation/1.0 (homelab deployment)"
        }

    def _search_recording_url(self):
        return f"{self.base_url}/recording"

    def _escape_query(self, text):
        if not text:
            return ""
        return text.replace('"', '').strip()

    def _strip_parenthetical(self, text):
        if not text:
            return text
        result = text
        while '(' in result and ')' in result:
            start = result.index('(')
            end = result.index(')', start)
            result = (result[:start] + result[end + 1:]).strip()
        return result

    def _normalize_search_terms(self, text):
        if not text:
            return []
        cleaned = text.strip()
        stripped = self._strip_parenthetical(cleaned).strip()
        results = [cleaned]
        if stripped and stripped != cleaned:
            results.append(stripped)
        return list(dict.fromkeys(results))

    def search_recording(self, artist, title, limit=5):
        title_variants = self._normalize_search_terms(title)
        artist_variants = self._normalize_search_terms(artist)
        queries = []

        for title_variant in title_variants:
            for artist_variant in artist_variants:
                if title_variant and artist_variant:
                    queries.append(f'recording:"{self._escape_query(title_variant)}" AND artist:"{self._escape_query(artist_variant)}"')
        for title_variant in title_variants:
            if title_variant:
                queries.append(f'recording:"{self._escape_query(title_variant)}"')
                queries.append(f'"{self._escape_query(title_variant)}"')
        for artist_variant in artist_variants:
            if artist_variant:
                queries.append(f'artist:"{self._escape_query(artist_variant)}"')
                queries.append(f'"{self._escape_query(artist_variant)}"')
        if title_variants and artist_variants:
            queries.append(f'"{self._escape_query(title_variants[0])}" {self._escape_query(artist_variants[0])}')
            queries.append(f'"{self._escape_query(artist_variants[0])}" {self._escape_query(title_variants[0])}')

        for query in queries:
            params = {
                "fmt": "json",
                "query": query,
                "limit": limit,
                "inc": "artist-credits+releases"
            }
            try:
                rate_limit()
                response = requests.get(self._search_recording_url(), headers=self.headers, params=params, timeout=10)
                response.raise_for_status()
                recordings = response.json().get("recordings", [])
                if recordings:
                    return recordings
            except requests.exceptions.RequestException as e:
                logger.warning(f"MusicBrainz recording search failed for query '{query}': {e}")
                continue

        return []

    def get_best_release_metadata(self, artist, title):
        recordings = self.search_recording(artist, title, limit=5)
        if not recordings:
            return None

        match = self._select_best_recording(artist, title, recordings)
        if not match:
            match = recordings[0]

        return self._extract_release_metadata(match)

    def _normalize(self, text):
        if not text:
            return ""
        return text.lower().strip()

    def _select_best_recording(self, artist, title, recordings):
        title_norm = self._normalize(title)
        artist_norm = self._normalize(artist)

        for recording in recordings:
            if self._normalize(recording.get("title")) != title_norm:
                continue

            credits = recording.get("artist-credit", [])
            if any(self._normalize(self._artist_credit_name(ac)) == artist_norm for ac in credits):
                return recording

        return None

    def _artist_credit_name(self, credit):
        if isinstance(credit, dict):
            artist = credit.get("artist", {})
            return artist.get("name") or credit.get("name") or ""
        return str(credit or "")

    def _join_artist_credit(self, credits):
        if not credits:
            return None

        names = []
        for credit in credits:
            name = self._artist_credit_name(credit)
            if name:
                names.append(name)

        return ", ".join(names) if names else None

    def _extract_release_metadata(self, recording):
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
            key=lambda release: release.get("date") or "9999-99-99"
        )
        release = releases[0]

        metadata["album"] = release.get("title")
        release_date = release.get("date")
        metadata["release_date"] = release_date or None
        metadata["year"] = release_date.split("-")[0] if release_date else None
        metadata["album_artist"] = self._join_artist_credit(release.get("artist-credit") or recording.get("artist-credit", []))

        for medium in release.get("media", []):
            for track in medium.get("track-list", []):
                recording_ref = track.get("recording", {})
                if recording_ref.get("id") == recording.get("id"):
                    metadata["track_position"] = track.get("position")
                    return metadata

        return metadata
