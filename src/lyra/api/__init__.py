"""
API module: External service clients for music automation.

Provides abstractions for:
- ListenBrainzClient: Fetch recommendations with recording_mbid
- MusicBrainzClient: Resolve metadata via MBID
- LidarrClient: Artist management and monitoring
"""

from .base import BaseAPIClient
from .listenbrainz import ListenBrainzClient
from .musicbrainz import MusicBrainzClient
from .lidarr import LidarrClient

__all__ = [
    "BaseAPIClient",
    "ListenBrainzClient",
    "MusicBrainzClient",
    "LidarrClient"
]
