# Lyra Architecture

## System Overview

Lyra is a self-hosted music automation system that synchronizes music recommendations from ListenBrainz with a local Navidrome music library, with fallback YouTube downloads for missing tracks.

### High-Level Flow

```
┌─────────────────────┐
│  ListenBrainz API   │
│  (Recommendations)  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────┐
│  ApplicationOrchestrator     │
│  ┌─────────────────────────┐│
│  │ 1. Fetch Recommendations││
│  │ 2. Add Artists (Lidarr) ││
│  │ 3. Track Recommended    ││
│  │    Tracks (for Fallback)││
│  └─────────────────────────┘│
└──┬───────────────────────┬──┘
   │                       │
   ▼                       ▼
┌──────────────────┐  ┌─────────────────┐
│  Lidarr API      │  │ Local Database  │
│  (Artist Add)    │  │ ┌─────────────┐ │
│                  │  │ │ Local Lib   │ │
└──────────────────┘  │ │ Tracked     │ │
                      │ │ Tracks      │ │
                      │ │ Artists     │ │
                      │ └─────────────┘ │
                      └─────────────────┘
                           │
                           ▼ (24-hour polling)
                    ┌──────────────────┐
                    │ FallbackOrchestr │
                    │ (YouTube Fallback)
                    └─────────┬────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │ YouTube (yt-dlp) │
                    │ + Local Library  │
                    └──────────────────┘
```

## Module Structure

```
src/lyra/
├── __init__.py              # Package definition
├── main.py                  # CLI entry point
├── config.py                # Configuration management
├── logging_config.py        # Logging setup
├── utils.py                 # Utility functions
│
├── database/                # Database abstraction layer
│   ├── __init__.py
│   ├── connection.py        # DatabaseConnection singleton
│   ├── state.py             # StateManager (library & artist ops)
│   ├── track_manager.py     # TrackStateManager (pending tracks)
│   └── library_scanner.py   # LibraryScanner (filesystem indexing)
│
├── api/                     # API client abstraction layer
│   ├── __init__.py
│   ├── base.py              # BaseAPIClient abstract class
│   ├── listenbrainz.py      # ListenBrainzClient
│   ├── musicbrainz.py       # MusicBrainzClient
│   └── lidarr.py            # LidarrClient
│
├── downloader/              # Downloader and metadata management
│   ├── __init__.py
│   ├── downloader.py        # Core Downloader (yt-dlp wrapper)
│   ├── metadata_handler.py  # MetadataHandler (ID3 tagging)
│   └── fallback_orchestrator.py  # FallbackOrchestrator (24-hour logic)
│
├── orchestrator/            # Main application orchestration
│   ├── __init__.py
│   └── orchestrator.py      # ApplicationOrchestrator
│
└── cli/                     # Command-line interface
    ├── __init__.py
    ├── interactive.py       # InteractiveDownloader
    └── db_integration.py    # CLIDatabaseIntegration
```

## Core Components

### 1. Database Layer (src/lyra/database/)

**Purpose**: Centralized music library state management

**DatabaseConnection** (`connection.py`):
- Singleton pattern for SQLite connection management
- Manages three tables: `artists`, `local_library`, `tracked_tracks`
- Creates indexes for performance
- Thread-safe context manager

**Schema**:

```sql
-- Artists tracked for recommendations
CREATE TABLE artists (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_checked TIMESTAMP
);

-- Local library cache (filesystem scanned tracks)
CREATE TABLE local_library (
    id INTEGER PRIMARY KEY,
    artist TEXT NOT NULL,
    album TEXT,
    title TEXT NOT NULL,
    recording_mbid TEXT,
    file_path TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(artist, title, recording_mbid)
);

-- Recommended tracks pending fallback download (24-hour timer)
CREATE TABLE tracked_tracks (
    id INTEGER PRIMARY KEY,
    recording_mbid TEXT,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT DEFAULT 'listenbrainz',
    status TEXT DEFAULT 'PENDING',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(recording_mbid, artist, title)
);
```

**StateManager** (`state.py`):
- High-level library operations (add_local_track, track_exists, clear_local_library)
- Artist tracking (add_artist, get_tracked_artists)
- Library indexing with force-rescan support

**TrackStateManager** (`track_manager.py`):
- Manages tracked_tracks table
- Adds pending tracks for fallback
- Retrieves tracks overdue for fallback (24-hour threshold)
- Marks completion by track_id or MBID

**LibraryScanner** (`library_scanner.py`):
- Filesystem scanning for audio files
- ID3 metadata extraction
- Fallback to filesystem path inference
- Force-rescan support

### 2. API Layer (src/lyra/api/)

**Purpose**: Abstraction over external API integrations

**BaseAPIClient** (`base.py`):
- Abstract base class with common functionality
- GET/POST methods with error handling
- Rate limiting (configurable delay between requests)
- Timeout management
- Comprehensive logging

**ListenBrainzClient** (`listenbrainz.py`):
- Fetches user recommendations with recording_mbid
- Resolves MBIDs to artist/title via MusicBrainz
- Methods:
  - `get_recommended_recording_mbids()`: Raw MBID list
  - `get_recommended_artists_with_tracks()`: Artist → [tracks] dict
  - `get_top_tracks_for_artist()`: Artist's top tracks

**MusicBrainzClient** (`musicbrainz.py`):
- Comprehensive recording search
- Normalizes queries and handles title/artist variants
- Best-match selection algorithm
- Release metadata extraction (album, year, track position)

**LidarrClient** (`lidarr.py`):
- Artist search and addition
- Missing content monitoring
- Configured with quality/metadata profile IDs

### 3. Downloader Layer (src/lyra/downloader/)

**Purpose**: Audio downloading and metadata management

**Downloader** (`downloader.py`):
- yt-dlp integration for YouTube search/download
- MP3 conversion with quality settings
- Artist/album folder creation (case-insensitive)
- File deduplication checks
- Methods:
  - `download_track(artist, track, dry_run)`: Automated download
  - `download_manual(url, artist, title, ...)`: URL with metadata
  - `get_search_results(query, limit)`: YouTube search

**MetadataHandler** (`metadata_handler.py`):
- ID3 tag application to audio files
- Supports: artist, title, album, album_artist, year, release_date, track_number
- Graceful tag initialization for various audio formats

**FallbackOrchestrator** (`fallback_orchestrator.py`):
- Checks pending tracks after 24-hour threshold
- Verifies local existence
- Downloads via YouTube if missing
- Updates local_library and marks tracked_tracks COMPLETED

### 4. Orchestrator Layer (src/lyra/orchestrator/)

**Purpose**: Main application logic coordination

**ApplicationOrchestrator** (`orchestrator.py`):
- Integrates all components
- Main workflow:
  1. Fetch recommendations from ListenBrainz
  2. Add artists to Lidarr
  3. Track recommended tracks for fallback
  4. Run fallback downloads after 24 hours
- Methods:
  - `add_artists_from_recommendations()`: Add new artists
  - `run_fallback()`: Execute YouTube fallback
  - `ensure_library_indexed()`: Scan and cache local library

### 5. CLI Layer (src/lyra/cli/)

**Purpose**: Interactive and command-line interfaces

**InteractiveDownloader** (`interactive.py`):
- Menu-driven interface with Rich formatting
- YouTube search
- Direct URL download
- Metadata entry and validation

**CLIDatabaseIntegration** (`db_integration.py`):
- Records manual downloads to local_library
- Integrates CLI operations with database

## Data Flow

### Workflow 1: Artist Addition from Recommendations

```
1. ListenBrainzClient.get_recommended_artists_with_tracks()
   ↓ (Returns: {artist: [tracks]})
   ↓
2. ApplicationOrchestrator.add_artists_from_recommendations()
   ├─ Filter existing artists in Lidarr
   ├─ Filter already tracked artists
   ├─ Add new artist to Lidarr
   └─ StateManager.add_artist() → artists table
   ↓
3. Track recommended tracks
   └─ TrackStateManager.add_tracked_track() → tracked_tracks (PENDING)
```

### Workflow 2: 24-Hour Fallback Polling

```
1. ApplicationOrchestrator.run_fallback()
   ↓
2. TrackStateManager.get_pending_tracked_tracks()
   └─ SELECT * FROM tracked_tracks WHERE status='PENDING' AND added_at < NOW-24h
   ↓
3. For each pending track:
   ├─ StateManager.track_exists() → check local_library
   ├─ If not found:
   │  ├─ Downloader.download_track() → YouTube yt-dlp
   │  ├─ MetadataHandler.apply_metadata() → ID3 tags
   │  ├─ StateManager.add_local_track() → local_library
   │  └─ TrackStateManager.mark_tracked_track_completed() → status=COMPLETED
   └─ If found locally:
      └─ TrackStateManager.mark_tracked_track_completed() → status=COMPLETED
```

### Workflow 3: Local Library Indexing

```
1. StateManager.ensure_library_indexed(force_rescan=False)
   ↓
2. LibraryScanner.scan_library()
   ├─ Walk LIBRARY_PATH
   ├─ Find audio files (mp3, flac, m4a, aac, ogg, opus, wav)
   ├─ Extract ID3 metadata or infer from filename
   └─ StateManager.add_local_track() → local_library
   ↓
3. Indexes created for fast lookups
   └─ CREATE INDEX on (artist, title, recording_mbid)
```

## Configuration

All configuration via environment variables (see `.env.example`):

- **ListenBrainz**: API token, server URL
- **Lidarr**: API key, server URL, quality profile ID
- **Paths**: LIBRARY_PATH, DB_PATH, DOWNLOAD_FOLDER
- **Thresholds**: MAX_ARTISTS_PER_RUN, FALLBACK_HOURS, API_RATE_LIMIT_DELAY

## Key Design Principles

1. **Separation of Concerns**: Each module has single responsibility
2. **Singleton Database**: Single DatabaseConnection for all components
3. **Type Hints**: Full type annotations throughout
4. **Error Handling**: Comprehensive try-catch with logging
5. **Rate Limiting**: Respects API rate limits
6. **Idempotency**: Safe to run multiple times without duplicates
7. **Cache-First**: Always checks local_library before external APIs
