from unittest.mock import MagicMock, patch

from lyra.api.lidarr import LidarrClient
from lyra.config import Config


@patch("lyra.api.base.requests.get")
@patch("lyra.api.base.requests.post")
def test_add_artist_payload(mock_post, mock_get):
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

    mock_post_response = MagicMock()
    mock_post_response.json.return_value = {"id": 1}
    mock_post_response.status_code = 201
    mock_post.return_value = mock_post_response

    client = LidarrClient(url="http://mock-lidarr:8686", api_key="mock-api-key")
    success = client.add_artist("AUDREY NUNA")

    assert success is True

    _, kwargs = mock_post.call_args
    payload = kwargs["json"]

    assert payload["artistName"] == "AUDREY NUNA"
    assert payload["artistType"] == "person"
    assert payload["qualityProfileId"] == Config.QUALITY_PROFILE_ID
    assert payload["metadataProfileId"] == Config.METADATA_PROFILE_ID
    assert payload["rootFolderPath"] == Config.LIDARR_ROOT_FOLDER_PATH
    assert payload["monitored"] is True
    assert payload["addOptions"]["monitor"] == "all"
    assert payload["addOptions"]["searchForMissingAlbums"] is True


def test_api_key_header_is_empty_string_when_key_unset(monkeypatch):
    """An unset key must send ``""``, not ``None`` (which ``requests`` rejects)."""
    monkeypatch.setattr(Config, "LIDARR_API_KEY", None)
    client = LidarrClient(url="http://mock-lidarr:8686", api_key=None)
    assert client.headers["X-Api-Key"] == ""
