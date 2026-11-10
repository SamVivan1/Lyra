"""
Database connection and initialization utilities.

Handles SQLite connections and schema initialization.
"""

import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional, Union

from ..config import Config

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """
    SQLite connection factory with an explicit two-phase lifecycle.

    The constructor only records the database path; it never touches disk, so
    constructing many instances in tests is free and hermetic. Call
    ``initialize()`` once before use to create the parent directory, apply the
    concurrency PRAGMAs and build the schema.
    """

    def __init__(self, path: Optional[Union[str, Path]] = None) -> None:
        """
        Record the database path without touching disk.

        Args:
            path: Database file path. Defaults to ``Config.DB_PATH``.
        """
        self.path = Path(path or Config.DB_PATH)
        self._initialized = False

    def initialize(self) -> None:
        """
        Create the database file, apply concurrency PRAGMAs and the schema.

        Runs ``PRAGMA journal_mode=WAL``, ``PRAGMA busy_timeout=5000`` and
        ``PRAGMA synchronous=NORMAL`` in that order, then initializes the
        schema. Must be called before ``get_connection()`` is used.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._initialize_schema()
        self._initialized = True
        logger.info(f"Database initialized at {self.path}")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Get a thread-safe database connection context manager.
        
        Yields:
            sqlite3.Connection: Database connection with Row factory enabled.
        """
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        """Initialize or migrate database schema."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Artists tracking table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS artists (
                        artist_name TEXT PRIMARY KEY,
                        added_at TEXT NOT NULL,
                        fallback_done INTEGER DEFAULT 0,
                        recommended_tracks TEXT
                    )
                ''')

                # Local library cache table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS local_library (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        artist TEXT NOT NULL,
                        album TEXT,
                        title TEXT NOT NULL,
                        recording_mbid TEXT,
                        file_path TEXT,
                        added_at TEXT NOT NULL,
                        UNIQUE(artist, title, recording_mbid)
                    )
                ''')

                # Tracked tracks for fallback table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS tracked_tracks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        recording_mbid TEXT,
                        artist TEXT NOT NULL,
                        title TEXT NOT NULL,
                        source TEXT DEFAULT 'listenbrainz',
                        status TEXT DEFAULT 'PENDING',
                        added_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(recording_mbid, artist, title)
                    )
                ''')

                # Create indexes for faster queries
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_local_library_artist ON local_library(artist)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_local_library_mbid ON local_library(recording_mbid)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_tracked_tracks_status ON tracked_tracks(status)')

                conn.commit()
                logger.debug("Database schema initialized/verified")
        except sqlite3.DatabaseError as e:
            logger.warning(f"Database file appears invalid: {self.path}. Attempting recovery. Error: {e}")
            if self.path.exists():
                backup_path = self.path.with_name(f"{self.path.stem}.corrupt{int(self.path.stat().st_mtime)}{self.path.suffix}")
                self.path.rename(backup_path)
                logger.warning(f"Renamed corrupt database to {backup_path}")
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS artists (
                        artist_name TEXT PRIMARY KEY,
                        added_at TEXT NOT NULL,
                        fallback_done INTEGER DEFAULT 0,
                        recommended_tracks TEXT
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS local_library (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        artist TEXT NOT NULL,
                        album TEXT,
                        title TEXT NOT NULL,
                        recording_mbid TEXT,
                        file_path TEXT,
                        added_at TEXT NOT NULL,
                        UNIQUE(artist, title, recording_mbid)
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS tracked_tracks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        recording_mbid TEXT,
                        artist TEXT NOT NULL,
                        title TEXT NOT NULL,
                        source TEXT DEFAULT 'listenbrainz',
                        status TEXT DEFAULT 'PENDING',
                        added_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(recording_mbid, artist, title)
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_local_library_artist ON local_library(artist)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_local_library_mbid ON local_library(recording_mbid)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_tracked_tracks_status ON tracked_tracks(status)')
                conn.commit()
                logger.debug("Recovered and reinitialized database schema")
