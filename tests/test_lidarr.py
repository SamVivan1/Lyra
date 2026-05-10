import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Add the project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from lidarr import LidarrClient
from config import Config

class TestLidarrClient(unittest.TestCase):
    def setUp(self):
        self.client = LidarrClient(url="http://mock-lidarr:8686", api_key="mock-api-key")

    @patch('lidarr.requests.get')
    @patch('lidarr.requests.post')
    def test_add_artist_payload(self, mock_post, mock_get):
        # Mock the search result from Lidarr lookup
        mock_search_response = MagicMock()
        mock_search_response.json.return_value = [
            {
                "artistName": "AUDREY NUNA",
                "artistType": "person",
                "foreignArtistId": "mbid-123",
                "images": [{"url": "http://image.url"}],
                "links": [{"url": "http://link.url"}],
                "genres": ["Pop"]
            }
        ]
        mock_search_response.status_code = 200
        mock_get.return_value = mock_search_response

        # Mock the POST response
        mock_post_response = MagicMock()
        mock_post_response.json.return_value = {"id": 1}
        mock_post_response.status_code = 201
        mock_post.return_value = mock_post_response

        # Execute add_artist
        success = self.client.add_artist("AUDREY NUNA")

        self.assertTrue(success)
        
        # Verify the POST payload
        args, kwargs = mock_post.call_args
        payload = kwargs['json']
        
        self.assertEqual(payload['artistName'], "AUDREY NUNA")
        self.assertEqual(payload['artistType'], "person")
        self.assertEqual(payload['qualityProfileId'], Config.QUALITY_PROFILE_ID)
        self.assertEqual(payload['metadataProfileId'], Config.METADATA_PROFILE_ID)
        self.assertEqual(payload['rootFolderPath'], Config.ROOT_FOLDER_PATH)
        self.assertTrue(payload['monitored'])
        self.assertEqual(payload['addOptions']['monitor'], "all")
        self.assertTrue(payload['addOptions']['searchForMissingAlbums'])

if __name__ == '__main__':
    unittest.main()
