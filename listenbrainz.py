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

    def _recommendation_urls(self):
        return [
            f"{self.base_url}/recommendation/user/{self.username}/recording",
            f"{self.base_url}/recommendation/user/{self.username}"
        ]

    def get_recommended_artists(self):
        """Fetch recommended artists from ListenBrainz for the configured user."""
        if not self.username:
            logger.error("ListenBrainz username is not configured.")
            return []

        artists = set()
        recordings = []
        for url in self._recommendation_urls():
            logger.info(f"Fetching recommendations from {url}")
            try:
                rate_limit()
                response = requests.get(url, headers=self.headers, timeout=10)
                if response.status_code == 404:
                    logger.warning(f"ListenBrainz endpoint not found: {url}")
                    continue
                response.raise_for_status()

                data = response.json()
                recordings = data.get("payload", {}).get("recordings", [])
                if recordings:
                    break
            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching recommendations from ListenBrainz: {e}")
                return []

        if not recordings:
            logger.info("No recommendation recordings found from ListenBrainz.")
            return []

        for rec in recordings:
            artist_credits = rec.get("artist_credit", [])
            for ac in artist_credits:
                artist_name = ac.get("artist_name")
                if artist_name:
                    artists.add(artist_name)

        return list(artists)

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
