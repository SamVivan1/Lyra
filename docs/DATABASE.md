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
│  │ status (PENDING,      │                       │
│  │         COMPLETED)    │                       │
│  │ added_at              │                       │
│  │ completed_at          │                       │
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
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(artist, title, recording_mbid)
);

CREATE INDEX idx_local_lib_artist ON local_library(artist);
CREATE INDEX idx_local_lib_artist_title ON local_library(artist, title);
CREATE INDEX idx_local_lib_mbid ON local_library(recording_mbid);
```

**Columns**:
- `id`: Primary key, auto-increment
- `artist`: Artist name
- `album`: Album name (nullable)
- `title`: Track title
- `recording_mbid`: MusicBrainz recording ID (nullable)
- `file_path`: Full path to audio file (nullable)
- `added_at`: Timestamp when track was added to cache

**Constraints**:
- UNIQUE constraint on (artist, title, recording_mbid) prevents duplicates
- Null values allowed for album, recording_mbid, file_path

**Purpose**: Cache-first lookup to avoid repeated filesystem scans

**Index Strategy**:
- Fast lookup by artist name
- Fast lookup by artist + title combination (most common)
- Fast lookup by MusicBrainz ID (for alternative searches)

### tracked_tracks

Tracks recommended tracks pending fallback download after 24-hour delay.

```sql
CREATE TABLE tracked_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_mbid TEXT,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT DEFAULT 'listenbrainz',
    status TEXT DEFAULT 'PENDING',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(recording_mbid, artist, title)
);

CREATE INDEX idx_tracked_status ON tracked_tracks(status);
CREATE INDEX idx_tracked_added_at ON tracked_tracks(added_at);
CREATE INDEX idx_tracked_status_added ON tracked_tracks(status, added_at);
```

**Columns**:
- `id`: Primary key, auto-increment
- `recording_mbid`: MusicBrainz recording ID (nullable, can be null initially)
- `artist`: Artist name
- `title`: Track title
- `source`: Source of recommendation (default: 'listenbrainz')
- `status`: State (PENDING → COMPLETED)
- `added_at`: Timestamp when track was added to tracking
- `completed_at`: Timestamp when fallback completed

**Constraints**:
- UNIQUE constraint on (recording_mbid, artist, title) prevents duplicates

**Purpose**: Implements 24-hour fallback queue

**Index Strategy**:
- Fast lookup by status (for pending queries)
- Fast lookup by added_at (for age-based filtering)
- Composite index on (status, added_at) for typical 24-hour query

## Query Examples

### Find pending tracks eligible for fallback (24 hours old)

```python
from datetime import datetime, timedelta

threshold = (datetime.now() - timedelta(hours=24)).isoformat()

query = """
SELECT id, recording_mbid, artist, title, source, added_at
FROM tracked_tracks
WHERE status = 'PENDING' 
  AND added_at < ?
ORDER BY added_at ASC
"""

results = db.execute(query, (threshold,)).fetchall()
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
# [('PENDING', 45), ('COMPLETED', 120)]
```

## Performance Considerations

### Index Usage

1. **Lookup by Artist (local_library)**:
   - Query: `WHERE artist = ?`
   - Index: `idx_local_lib_artist`
   - Expected: O(log n)

2. **Lookup by Artist + Title (local_library)**:
   - Query: `WHERE artist = ? AND title = ?`
   - Index: `idx_local_lib_artist_title`
   - Expected: O(log n)

3. **Pending Tracks Query (tracked_tracks)**:
   - Query: `WHERE status = 'PENDING' AND added_at < ?`
   - Index: `idx_tracked_status_added`
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
