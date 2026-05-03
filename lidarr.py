import requests
from config import Config
from utils import setup_logger, rate_limit

logger = setup_logger("lidarr")

class LidarrClient:
    def __init__(self, url=Config.LIDARR_URL, api_key=Config.LIDARR_API_KEY):
        self.url = url
        self.api_key = api_key
        self.headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json"
        }

    def _get(self, endpoint, params=None):
        try:
            rate_limit()
            response = requests.get(f"{self.url}{endpoint}", headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Lidarr GET {endpoint} failed: {e}")
            return None

    def _post(self, endpoint, json_data):
        try:
            rate_limit()
            response = requests.post(f"{self.url}{endpoint}", headers=self.headers, json=json_data, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Lidarr POST {endpoint} failed: {e}")
            return None

    def get_existing_artists(self):
        """Retrieve all artists currently in Lidarr."""
        logger.info("Fetching existing artists from Lidarr")
        data = self._get("/api/v1/artist")
        if data is None:
            return []
        
        # Lidarr returns a list of artists
        return [artist.get("artistName", "") for artist in data]

    def add_artist(self, artist_name):
        """Add a new artist to Lidarr."""
        # First, search Lidarr to get the proper metadata
        logger.info(f"Searching Lidarr for artist: {artist_name}")
        search_results = self._get("/api/v1/artist/lookup", params={"term": artist_name})
        
        if not search_results:
            logger.warning(f"Artist '{artist_name}' not found in Lidarr search.")
            return False
            
        # Take the first exact or best match
        best_match = search_results[0]
        
        payload = {
            "artistName": best_match.get("artistName"),
            "qualityProfileId": Config.QUALITY_PROFILE_ID,
            "metadataProfileId": Config.METADATA_PROFILE_ID,
            "rootFolderPath": Config.ROOT_FOLDER_PATH,
            "monitored": True,
            "addOptions": {
                "searchForMissingAlbums": True
            },
            # Lidarr requires some existing fields from the lookup
            "foreignArtistId": best_match.get("foreignArtistId"),
            "images": best_match.get("images", []),
            "links": best_match.get("links", []),
            "genres": best_match.get("genres", [])
        }
        
        logger.info(f"Adding artist '{best_match.get('artistName')}' to Lidarr")
        result = self._post("/api/v1/artist", json_data=payload)
        
        if result:
            logger.info(f"Successfully added artist '{best_match.get('artistName')}'")
            return True
        return False

    def get_missing_artists(self):
        """Find artists that have missing albums."""
        logger.info("Fetching missing content from Lidarr")
        data = self._get("/api/v1/wanted/missing", params={"pageSize": 1000}) # adjust pageSize as needed
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
