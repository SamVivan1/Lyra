# Database Documentation

## Schema Overview

Lyra uses SQLite3 with three main tables for state management:

```
┌──────────────────────────────────────────────────┐
│                   SCHEMA DIAGRAM                 │
├──────────────────────────────────────────────────┤
│                                                  │
│  artists                 local_library            │
│  ┌─────────────┐        ┌──────────────┐         │
│  │ id (PK)     │        │ id (PK)      │         │
│  │ name (UQ)   │────┐   │ artist       │         │
│  │ added_at    │    │   │ album        │         │
│  │ last_checked│    │   │ title        │         │
│  └─────────────┘    │   │ recording_   │         │
│                     │   │   mbid       │         │
│                     │   │ file_path    │         │
│                     │   │ added_at     │         │
│                     └───│ (FK to       │         │
│                         │  artists.id) │         │
│                         └──────────────┘         │
│                                                  │
│  tracked_tracks                                  │
│  ┌───────────────────────┐                       │
│  │ id (PK)               │                       │
│  │ recording_mbid        │                       │
│  │ artist                │                       │
│  │ title                 │                       │
│  │ source (listenbrainz) │                       │
│  │ status (NEW,          │                       │
│  │   QUEUED_FALLBACK,    │                       │
│  │   RUNNING, COMPLETED, │                       │
│  │   WAITING_LIDARR,     │                       │
│  │   FAILED, DEAD)       │                       │
│  │ added_at              │                       │
│  │ updated_at            │                       │
│  │ attempts              │                       │
│  │ last_attempt_at       │                       │
│  │ next_retry_at         │                       │
│  │ last_error            │                       │
│  │ lidarr_checked_at     │                       │
│  └───────────────────────┘                       │
│                                                  │
└──────────────────────────────────────────────────┘
```

## Tables

### artists

Tracks which artists are being monitored for recommendations.

```sql
CREATE TABLE artists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_checked TIMESTAMP
);

CREATE INDEX idx_artists_name ON artists(name);
CREATE INDEX idx_artists_added_at ON artists(added_at);
```

**Columns**:
- `id`: Primary key, auto-increment
- `name`: Artist name (unique constraint prevents duplicates)
- `added_at`: Timestamp when artist was added to tracking
- `last_checked`: Last time artist was checked for recommendations

**Purpose**: Maintains set of artists to monitor

### local_library

Cache of local music library files.

```sql
CREATE TABLE local_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist TEXT NOT NULL,
    album TEXT,
    title TEXT NOT NULL,
    recording_mbid TEXT,
    file_path TEXT,
    added_at TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_local_library_natural
    ON local_library(artist COLLATE NOCASE, title COLLATE NOCASE);
CREATE INDEX idx_local_library_artist ON local_library(artist);
CREATE INDEX idx_local_library_mbid ON local_library(recording_mbid);
```

**Columns**:
- `id`: Primary key, auto-increment
- `artist`: Artist name
- `album`: Album name (nullable)
- `title`: Track title
- `recording_mbid`: MusicBrainz recording ID (nullable)
- `file_path`: Full path to audio file (nullable)
- `added_at`: Timestamp when track was added to cache; a re-scan never rewrites it

**Constraints**:
- The natural key is a UNIQUE index on `(artist COLLATE NOCASE, title COLLATE NOCASE)`. `recording_mbid` is an attribute, not part of the key: a NULL MBID made the old table-level constraint useless.
- Null values allowed for album, recording_mbid, file_path
- Writing an existing artist + title is an upsert: `file_path` is refreshed, `album`/`recording_mbid` are only filled in, and `added_at` is kept

**Purpose**: Cache-first lookup to avoid repeated filesystem scans

**Index Strategy**:
- Unique natural-key index makes the artist + title write idempotent, and a re-scan of a moved file updates `file_path` instead of leaving the stale one
- Fast lookup by artist name
- Fast lookup by MusicBrainz ID (for alternative searches)

### tracked_tracks

Tracks recommended tracks and their fallback download state.

```sql
CREATE TABLE tracked_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_mbid TEXT,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT DEFAULT 'listenbrainz',
    status TEXT NOT NULL DEFAULT 'NEW',
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_retry_at TEXT,
    last_error TEXT,
    lidarr_checked_at TEXT
);

CREATE UNIQUE INDEX ux_tracked_natural
    ON tracked_tracks(artist COLLATE NOCASE, title COLLATE NOCASE);
CREATE INDEX ix_tracked_queue ON tracked_tracks(status, next_retry_at);
```

**Columns**:
- `id`: Primary key, auto-increment
- `recording_mbid`: MusicBrainz recording ID (nullable; an attribute, not part of the key)
- `artist`: Artist name
- `title`: Track title
- `source`: Source of recommendation (default: 'listenbrainz')
- `status`: State machine value: `NEW`, `WAITING_LIDARR`, `QUEUED_FALLBACK`, `RUNNING`, `COMPLETED`, `FAILED`, `DEAD` (default: `NEW`)
- `added_at`: Timestamp when track was added to tracking (never rewritten)
- `updated_at`: Timestamp of the last status change
- `attempts`: Download attempts so far (default: 0)
- `last_attempt_at`: When the last attempt started (nullable)
- `next_retry_at`: Earliest time the backoff allows another attempt (nullable)
- `last_error`: The last failure, truncated to 500 characters (nullable)
- `lidarr_checked_at`: When Lidarr was last consulted for this track (nullable)

**Constraints**:
- The natural key is a UNIQUE index on `(artist COLLATE NOCASE, title COLLATE NOCASE)`; `recording_mbid` is an attribute, not part of the key

**Purpose**: Implements the per-track fallback queue and its retry state

**Index Strategy**:
- Fast lookup of the fallback queue by status and the time it may run
- The composite index on `(status, next_retry_at)` covers the queue query below

## Query Examples

### Find tracks eligible for fallback (queue head, backoff elapsed)

```python
from datetime import datetime, timezone

now = datetime.now(timezone.utc).isoformat()

query = """
SELECT id, recording_mbid, artist, title, source, added_at, attempts
FROM tracked_tracks
WHERE status = 'QUEUED_FALLBACK'
  AND COALESCE(next_retry_at, added_at) <= ?
ORDER BY COALESCE(next_retry_at, added_at) ASC
LIMIT ?
"""

results = db.execute(query, (now, 10)).fetchall()
```

### Check if track exists locally

```python
query = """
SELECT id, file_path
FROM local_library
WHERE artist = ? AND title = ?
LIMIT 1
"""

result = db.execute(query, (artist, title)).fetchone()
exists = result is not None
```

### Search by MusicBrainz ID

```python
query = """
SELECT artist, title, file_path
FROM local_library
WHERE recording_mbid = ?
"""

result = db.execute(query, (recording_mbid,)).fetchone()
```

### Get all tracked artists

```python
query = """
SELECT name, added_at
FROM artists
ORDER BY added_at DESC
"""

artists = db.execute(query).fetchall()
```

### Count tracks by status

```python
query = """
SELECT status, COUNT(*) as count
FROM tracked_tracks
GROUP BY status
"""

stats = db.execute(query).fetchall()
# [('NEW', 45), ('QUEUED_FALLBACK', 3), ('COMPLETED', 120)]
```

## Performance Considerations

### Index Usage

1. **Lookup by Artist (local_library)**:
   - Query: `WHERE artist = ?`
   - Index: `idx_local_library_artist`
   - Expected: O(log n)

2. **Lookup by Artist + Title (local_library)**:
   - Query: `WHERE artist = ? AND title = ?`
   - Index: `ux_local_library_natural` (the natural-key unique index)
   - Expected: O(log n)

3. **Fallback Queue Query (tracked_tracks)**:
   - Query: `WHERE status = 'QUEUED_FALLBACK' AND COALESCE(next_retry_at, added_at) <= ?`
   - Index: `ix_tracked_queue` (`status`, `next_retry_at`)
   - Expected: O(log n)

### Vacuum and Maintenance

Periodically run to reclaim space:

```python
db.execute("VACUUM")
```

### Statistics

Analyze query optimization:

```python
db.execute("ANALYZE")
```

## DatabaseConnection Singleton

All database operations use the singleton pattern:

```python
from lyra.database.connection import DatabaseConnection

# Get or create singleton
db = DatabaseConnection()

# Use in context manager
with db.get_connection() as conn:
    cursor = conn.cursor()
    result = cursor.execute("SELECT ...")
```

### Thread Safety

The DatabaseConnection singleton is thread-safe:

```python
# Safe to use from multiple threads
db = DatabaseConnection()

# Each thread gets its own connection from the pool
with db.get_connection() as conn:
    # Connection is thread-local
    pass
```

## Migration and Backup

### Backup Database

```bash
cp lyra.db lyra.db.backup
```

### Reset Database

```python
from lyra.database.connection import DatabaseConnection

db = DatabaseConnection()
db.reset_database()  # Drops all tables and recreates schema
```

### Export to CSV

```python
import csv
import sqlite3

conn = sqlite3.connect('lyra.db')
cursor = conn.cursor()

# Export local_library
cursor.execute("SELECT * FROM local_library")
rows = cursor.fetchall()

with open('export.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerows(rows)
```
