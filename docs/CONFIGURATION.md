# Configuration

Lyra is configured through environment variables and an optional `.env` file.
The application loads `.env` from the current working directory when available.

## Required Variables

- `LISTENBRAINZ_USERNAME`: Your ListenBrainz username.
- `LISTENBRAINZ_API_TOKEN`: ListenBrainz API token.
- `LIDARR_API_KEY`: Lidarr API key.

## Optional Variables

- `LIDARR_URL`: Base URL for Lidarr. Defaults to `http://localhost:8686`.
- `LIDARR_ROOT_FOLDER_PATH`: Root path as Lidarr sees it. Defaults to `/music`.
- `LIBRARY_PATH`: Local music library path used by Lyra.
- `DOWNLOAD_PATH`: Download folder used by the fallback downloader.
- `DB_PATH`: SQLite database path. Defaults to `music_automation.db`.
- `MAX_ARTISTS_PER_RUN`: Maximum number of artists to add per session.
- `FALLBACK_HOURS`: Hours to wait before fallback downloads are attempted.
- `LOG_LEVEL`: Logging verbosity.
- `API_RATE_LIMIT_DELAY`: Time in seconds to wait between API requests.

## Example `.env`

```env
LISTENBRAINZ_USERNAME=your_username
LISTENBRAINZ_API_TOKEN=your_listenbrainz_api_token
LIDARR_URL=http://localhost:8686
LIDARR_API_KEY=your_lidarr_api_key
QUALITY_PROFILE_ID=1
METADATA_PROFILE_ID=1
LIDARR_ROOT_FOLDER_PATH=/music
LIBRARY_PATH=/mnt/Storage2/data/media/Music
DOWNLOAD_PATH=/DATA/Downloads
MAX_ARTISTS_PER_RUN=5
FALLBACK_HOURS=24
DB_PATH=music_automation.db
LOG_LEVEL=INFO
API_RATE_LIMIT_DELAY=2
```

## Path Mapping Notes

When running Lidarr inside Docker, `LIDARR_ROOT_FOLDER_PATH` should reflect the path inside the Lidarr container, while `LIBRARY_PATH` should point to the same volume path as seen by Lyra on the host.

Example:

- `LIDARR_ROOT_FOLDER_PATH=/music`
- `LIBRARY_PATH=/mnt/Storage2/data/media/Music`
