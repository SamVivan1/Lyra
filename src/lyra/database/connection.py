"""
Database connection and initialization utilities.

Handles SQLite connection pooling and schema migrations.
"""

import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from ..config import Config

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """
    Singleton database connection manager with schema initialization.
    
    Thread-safe database connection factory that manages SQLite schema
    creation and migrations.
    """

    _instance: Optional['DatabaseConnection'] = None
    _db_path: Optional[Path] = None
    _initialized: bool = False

    def __new__(cls, db_path: Optional[str] = None) -> 'DatabaseConnection':
        """Ensure singleton pattern across the application."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: Optional[str] = None) -> None:
        """Initialize database connection and schema if first instantiation."""
        if self._initialized:
            if db_path and self._db_path != Path(db_path):
                self._db_path = Path(db_path)
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                self._initialize_schema()
                logger.info(f"Database reinitialized for path {self._db_path}")
            return

        self._db_path = Path(db_path or Config.DB_PATH)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._initialize_schema()
        self._initialized = True
        logger.info(f"Database initialized at {self._db_path}")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Get a thread-safe database connection context manager.
        
        Yields:
            sqlite3.Connection: Database connection with Row factory enabled.
        """
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
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
            logger.warning(f"Database file appears invalid: {self._db_path}. Attempting recovery. Error: {e}")
            if self._db_path.exists():
                backup_path = self._db_path.with_name(f"{self._db_path.stem}.corrupt{int(self._db_path.stat().st_mtime)}{self._db_path.suffix}")
                self._db_path.rename(backup_path)
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
