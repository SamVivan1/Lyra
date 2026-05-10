import requests
from config import Config
from utils import setup_logger, rate_limit

logger = setup_logger("listenbrainz")

class ListenBrainzClient:
    def __init__(self, username=Config.LISTENBRAINZ_USERNAME, base_url=Config.LISTENBRAINZ_API_URL, token=Config.LISTENBRAINZ_API_TOKEN):
        self.username = username
        self.base_url = base_url
        self.token = token
        self.headers = {
            "Accept": "application/json",
            "User-Agent": "MusicAutomation/1.0 ( homelab deployment )"
        }
        if self.token:
            self.headers["Authorization"] = f"Token {self.token}"

    def _recommendation_url(self):
        return f"{self.base_url}/cf/recommendation/user/{self.username}/recording"

    def _musicbrainz_recording_url(self, recording_mbid):
        return f"https://musicbrainz.org/ws/2/recording/{recording_mbid}"

    def get_recommended_recording_mbids(self, count=10, offset=0):
        """Fetch recommended recording MBIDs from ListenBrainz for the configured user."""
        if not self.username:
            logger.error("ListenBrainz username is not configured.")
            return []

        url = self._recommendation_url()
        params = {"count": count, "offset": offset}
        logger.info(f"Fetching recording recommendations from {url}")

        try:
            rate_limit()
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            if response.status_code == 204:
                logger.info("ListenBrainz returned 204 No Content for recording recommendations.")
                return []
            response.raise_for_status()

            data = response.json()
            mbids = [item.get("recording_mbid") for item in data.get("payload", {}).get("mbids", []) if item.get("recording_mbid")]
            return mbids
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching recording recommendations from ListenBrainz: {e}")
            return []

    def get_recording_metadata(self, recording_mbid):
        """Resolve a ListenBrainz recording MBID to artist names and track title via MusicBrainz."""
        url = self._musicbrainz_recording_url(recording_mbid)
        headers = {
            "Accept": "application/json",
            "User-Agent": "MusicAutomation/1.0 ( homelab deployment )"
        }
        params = {"fmt": "json", "inc": "artist-credits"}

        try:
            rate_limit()
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
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to resolve recording {recording_mbid} metadata: {e}")
            return [], None

    def get_recommended_artists_with_tracks(self):
        """Fetch recommended artists and their tracks from ListenBrainz."""
        recording_mbids = self.get_recommended_recording_mbids()
        if not recording_mbids:
            logger.info("No recommendations found from ListenBrainz.")
            return {}

        recommendations = {} # artist_name -> set(track_names)
        for mbid in recording_mbids:
            artist_names, track_title = self.get_recording_metadata(mbid)
            if not track_title:
                continue
            for artist_name in artist_names:
                if artist_name not in recommendations:
                    recommendations[artist_name] = set()
                recommendations[artist_name].add(track_title)

        # Convert sets to lists
        return {k: list(v) for k, v in recommendations.items()}

    def get_recommended_artists(self):
        """Fetch recommended artists from ListenBrainz for the configured user."""
        return list(self.get_recommended_artists_with_tracks().keys())

    def get_top_tracks_for_artist(self, artist_name, limit=2):
        """Fetch the user's listening history and find top tracks for a specific artist."""
        if not self.username:
            return []

        url = f"{self.base_url}/user/{self.username}/listens"
        # We might need to page through, but for simplicity we fetch the most recent ones.
        # If the user hasn't listened to the artist recently, we could also use a search endpoint, 
        # but the spec says "Use user listening history endpoint".
        # Another approach if history doesn't have it: we just get the track from recommendations.
        # Let's fetch a larger count to have a better chance.
        params = {"count": 100}
        
        logger.info(f"Fetching listening history to find tracks for '{artist_name}'")
        
        tracks = set()
        try:
            rate_limit()
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            listens = data.get("payload", {}).get("listens", [])
            
            for listen in listens:
                track_meta = listen.get("track_metadata", {})
                current_artist = track_meta.get("artist_name")
                
                # Case-insensitive match
                if current_artist and current_artist.lower() == artist_name.lower():
                    track_name = track_meta.get("track_name")
                    if track_name:
                        tracks.add(track_name)
                        if len(tracks) >= limit:
                            break
                            
            return list(tracks)
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching listening history for tracks: {e}")
            return []
