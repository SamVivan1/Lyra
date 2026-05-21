import os
import tempfile
from unittest.mock import patch

from lyra.api.musicbrainz import MusicBrainzClient
from lyra.downloader.downloader import Downloader
from lyra.cli.interactive import InteractiveDownloader


def test_extract_release_metadata_from_recording():
    client = MusicBrainzClient()
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

    metadata = client._extract_release_metadata(recording)

    assert metadata["album"] == "Test Album"
    assert metadata["year"] == "2022"
    assert metadata["release_date"] == "2022-01-01"
    assert metadata["album_artist"] == "Test Artist"
    assert metadata["track_position"] == 1


@patch.object(MusicBrainzClient, "search_recording")
def test_get_best_release_metadata_selects_matching_recording(mock_search):
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

    client = MusicBrainzClient()
    metadata = client.get_best_release_metadata("Test Artist", "Test Song")

    assert metadata["album"] == "Matched Album"
    assert metadata["year"] == "2023"
    assert metadata["track_position"] == 2


@patch.object(MusicBrainzClient, "search_recording")
def test_get_best_release_metadata_returns_none_for_no_recordings(mock_search):
    mock_search.return_value = []
    client = MusicBrainzClient()
    metadata = client.get_best_release_metadata("Test Artist", "Test Song")
    assert metadata is None


def test_safe_name_sanitizes_path_characters(tmp_path):
    downloader = Downloader(library_path=str(tmp_path))
    unsafe = 'Artist: Name?/Album*Test'
    safe = downloader._safe_name(unsafe)

    assert ':' not in safe
    assert '?' not in safe
    assert '/' not in safe
    assert '*' not in safe
    assert safe.startswith('Artist')


def test_find_or_create_album_folder_creates_nested_album_dir(tmp_path):
    downloader = Downloader(library_path=str(tmp_path))
    artist = 'Test Artist'
    album = 'Great Album: Edition'
    album_folder = downloader._find_or_create_album_folder(artist, album)

    assert os.path.isdir(album_folder)
    assert downloader._safe_name(album) in album_folder
    assert downloader._safe_name(artist) in album_folder


def test_parse_artist_title_from_youtube_label():
    cli = InteractiveDownloader()
    label = 'Multo - Cup of Joe (Official Lyric Video)'
    artist, title = cli._parse_artist_title_from_label(label, uploader='Cup of Joe')

    assert artist == 'Multo'
    assert title == 'Cup of Joe'


def test_parse_label_without_artist():
    cli = InteractiveDownloader()
    label = 'Cup of Joe'
    artist, title = cli._parse_artist_title_from_label(label, uploader='Cup of Joe')

    assert artist is None
    assert title is None
