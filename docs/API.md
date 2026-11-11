# API Clients Documentation

## BaseAPIClient

Abstract base class providing common functionality for all API clients.

### Features

- **Rate Limiting**: Configurable delay between requests (default: 2 seconds)
- **Error Handling**: Automatic retry logic with exponential backoff
- **Timeout Management**: Configurable request timeouts
- **Logging**: Comprehensive request/response logging

### Methods

```python
def get(self, url: str, params: dict = None) -> Optional[dict]:
    """Send GET request with rate limiting and error handling."""

def post(self, url: str, data: dict = None, json: dict = None) -> Optional[dict]:
    """Send POST request with rate limiting and error handling."""
```

## ListenBrainzClient

Fetches music recommendations from ListenBrainz.

### Configuration

```python
# Environment variables
LISTENBRAINZ_TOKEN=your_token_here
LISTENBRAINZ_SERVER=https://api.listenbrainz.org
```

### Methods

#### get_recommended_recording_mbids() → List[str]

Get raw list of recording MBIDs from recommendations.

**Returns**: List of MusicBrainz recording IDs

```python
from lyra.api import ListenBrainzClient

client = ListenBrainzClient()
mbids = client.get_recommended_recording_mbids()
# ['uuid1', 'uuid2', 'uuid3', ...]
```

#### get_recommended_artists_with_tracks() → Dict[str, List[str]]

Get recommendations grouped by artist with tracks.

**Returns**: Dictionary mapping artist name to list of track titles

```python
recommendations = client.get_recommended_artists_with_tracks()
# {
#     'The Beatles': ['Hey Jude', 'Let It Be'],
#     'Pink Floyd': ['Wish You Were Here', 'Comfortably Numb'],
#     ...
# }
```

#### get_top_tracks_for_artist(artist: str) → List[str]

Get top tracks for specific artist.

**Parameters**:
- `artist`: Artist name

**Returns**: List of track titles

```python
tracks = client.get_top_tracks_for_artist('The Beatles')
# ['Yesterday', 'Something', 'Come Together', ...]
```

### Rate Limiting

Default: 2 seconds between requests. Configure via:

```python
Config.API_RATE_LIMIT_DELAY = 1  # 1 second
```

## MusicBrainzClient

Resolves artist/title queries to recording metadata.

### Configuration

```python
# Environment variables
MUSICBRAINZ_SERVER=https://musicbrainz.org/ws/2
```

### Methods

#### search_recording(title: str, artist: str, album: Optional[str] = None) → Optional[Dict]

Search for recording metadata by title and artist.

**Parameters**:
- `title`: Track title
- `artist`: Artist name  
- `album`: Album name (optional)

**Returns**: Recording metadata dict or None

```python
from lyra.api import MusicBrainzClient

client = MusicBrainzClient()
result = client.search_recording('Hey Jude', 'The Beatles')
# {
#     'recording_mbid': 'uuid-here',
#     'title': 'Hey Jude',
#     'artist': 'The Beatles',
#     'album': 'Hey Jude',
#     'year': 2014,
#     'release_date': '2014-11-17',
#     'track_position': 1
# }
```

### Search Normalization

The client automatically:
- Removes parenthetical disambiguation (e.g., "(Remastered)" → removed)
- Tries title/artist combinations in different orders
- Handles special characters and encoding

## LidarrClient

Manages artist addition to Lidarr.

### Configuration

```python
# Environment variables
LIDARR_API_KEY=your_api_key
LIDARR_SERVER=http://localhost:8686
LIDARR_QUALITY_PROFILE_ID=1
LIDARR_METADATA_PROFILE_ID=1
LIDARR_ROOT_PATH=/music
```

### Methods

#### add_artist(artist: str) → bool

Add artist to Lidarr with quality settings.

**Parameters**:
- `artist`: Artist name

**Returns**: True if successfully added, False otherwise

```python
from lyra.api import LidarrClient

client = LidarrClient()
success = client.add_artist('The Beatles')
```

#### get_existing_artists() → List[str]

Get list of artists already in Lidarr.

**Returns**: List of artist names

```python
existing = client.get_existing_artists()
# ['The Beatles', 'Pink Floyd', 'Led Zeppelin', ...]
```

#### get_missing_tracks(artist_id: int) → List[Dict]

Get missing tracks for artist.

**Parameters**:
- `artist_id`: Lidarr artist ID

**Returns**: List of missing track info

### Rate Limiting

MusicBrainz has strict rate limits:
- Default: 2 seconds between requests
- Automatic backoff on 429 Too Many Requests

## Error Handling

All clients implement graceful error handling:

```python
from lyra.api import ListenBrainzClient

client = ListenBrainzClient()

try:
    recommendations = client.get_recommended_artists_with_tracks()
    if not recommendations:
        print("No recommendations available")
except Exception as e:
    print(f"API error: {e}")
    # Logged automatically
```

## Timeout Behavior

Default timeout: 30 seconds per request

Configure globally:
```python
Config.API_TIMEOUT = 60  # seconds
```

## Usage Example: Complete Integration

```python
from lyra.api import ListenBrainzClient, LidarrClient, MusicBrainzClient

# Initialize clients
lb_client = ListenBrainzClient()
lidarr_client = LidarrClient()
mb_client = MusicBrainzClient()

# 1. Get recommendations
recommendations = lb_client.get_recommended_artists_with_tracks()

# 2. Get existing artists in Lidarr
existing = set(a.lower() for a in lidarr_client.get_existing_artists())

# 3. Add new artists
for artist, tracks in recommendations.items():
    if artist.lower() not in existing:
        if lidarr_client.add_artist(artist):
            print(f"Added: {artist}")
            
            # 4. Resolve tracks to metadata
            for track in tracks:
                metadata = mb_client.search_recording(track, artist)
                if metadata:
                    print(f"  - {track} (MBID: {metadata['recording_mbid']})")
```
