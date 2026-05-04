import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, ROOT_DIR)

from musicbrainz import MusicBrainzClient
from downloader import Downloader


class TestMusicBrainzClient(unittest.TestCase):
    def setUp(self):
        self.client = MusicBrainzClient()

    def test_extract_release_metadata_from_recording(self):
        recording = {
            "id": "rec1",
            "title": "Test Song",
            "artist-credit": [{"artist": {"name": "Test Artist"}}],
            "releases": [
                {
                    "title": "Test Album",
                    "date": "2022-01-01",
                    "artist-credit": [{"artist": {"name": "Test Artist"}}],
                    "media": [
                        {
                            "track-list": [
                                {
                                    "position": 1,
                                    "recording": {"id": "rec1"}
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        metadata = self.client._extract_release_metadata(recording)

        self.assertEqual(metadata["album"], "Test Album")
        self.assertEqual(metadata["year"], "2022")
        self.assertEqual(metadata["release_date"], "2022-01-01")
        self.assertEqual(metadata["album_artist"], "Test Artist")
        self.assertEqual(metadata["track_position"], 1)

    @patch.object(MusicBrainzClient, "search_recording")
    def test_get_best_release_metadata_selects_matching_recording(self, mock_search):
        mock_search.return_value = [
            {
                "id": "rec1",
                "title": "Test Song",
                "artist-credit": [{"artist": {"name": "Other Artist"}}],
                "releases": []
            },
            {
                "id": "rec2",
                "title": "Test Song",
                "artist-credit": [{"artist": {"name": "Test Artist"}}],
                "releases": [
                    {
                        "title": "Matched Album",
                        "date": "2023-05-01",
                        "artist-credit": [{"artist": {"name": "Test Artist"}}],
                        "media": [
                            {
                                "track-list": [
                                    {
                                        "position": 2,
                                        "recording": {"id": "rec2"}
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

        metadata = self.client.get_best_release_metadata("Test Artist", "Test Song")

        self.assertEqual(metadata["album"], "Matched Album")
        self.assertEqual(metadata["year"], "2023")
        self.assertEqual(metadata["track_position"], 2)

    @patch.object(MusicBrainzClient, "search_recording")
    def test_get_best_release_metadata_returns_none_for_no_recordings(self, mock_search):
        mock_search.return_value = []
        metadata = self.client.get_best_release_metadata("Test Artist", "Test Song")
        self.assertIsNone(metadata)


class TestDownloader(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.downloader = Downloader(library_path=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_safe_name_sanitizes_path_characters(self):
        unsafe = 'Artist: Name?/Album*Test'
        safe = self.downloader._safe_name(unsafe)
        self.assertNotIn(':', safe)
        self.assertNotIn('?', safe)
        self.assertNotIn('/', safe)
        self.assertNotIn('*', safe)
        self.assertTrue(safe.startswith('Artist'))

    def test_find_or_create_album_folder_creates_nested_album_dir(self):
        artist = 'Test Artist'
        album = 'Great Album: Edition'
        album_folder = self.downloader._find_or_create_album_folder(artist, album)

        self.assertTrue(os.path.isdir(album_folder))
        self.assertIn(self.downloader._safe_name(album), album_folder)
        self.assertIn(self.downloader._safe_name(artist), album_folder)


if __name__ == '__main__':
    unittest.main()
