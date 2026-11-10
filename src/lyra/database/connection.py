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

#: Per-connection SQLite pragmas. ``busy_timeout`` and ``synchronous`` are
#: connection settings, so they must be applied to every new connection.
BUSY_TIMEOUT_MS = 5000
#: Journal mode the concurrency guarantee of this module depends on. It is a
#: file property: setting it once is enough, but the answer must be checked.
JOURNAL_MODE = "wal"


class DatabaseConnection:
    """
    SQLite connection factory with an explicit two-phase lifecycle.

    The constructor only records the database path; it never touches disk, so
    constructing many instances in tests is free and hermetic. Call
    ``initialize()`` once before use to create the parent directory, build the
    schema and apply the concurrency PRAGMAs.
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
        Create the database file, build the schema and apply concurrency PRAGMAs.

        The schema is built first, on purpose: ``_initialize_schema()`` is the
        only thing that knows how to recover from a corrupt file, and every
        PRAGMA below raises ``sqlite3.DatabaseError`` on such a file. Creating
        the schema (and, if needed, renaming the corrupt file and rebuilding)
        before the PRAGMAs keeps that recovery reachable; afterwards the
        PRAGMAs run on a fresh connection to the recovered file.

        Must be called before ``get_connection()`` is used.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        with self.get_connection() as conn:
            self._enable_wal(conn)
            self._apply_pragmas(conn)
        self._initialized = True
        logger.info(f"Database initialized at {self.path}")

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """Apply the per-connection concurrency pragmas."""
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous=NORMAL")

    def _enable_wal(self, conn: sqlite3.Connection) -> str:
        """
        Put the database file in WAL mode and return the mode SQLite reports.

        The returned row is checked rather than discarded: if the file cannot be
        switched to WAL, readers block on open writer transactions, which is the
        exact behaviour this module exists to avoid. A filesystem without WAL
        support is not fatal (the database still works), so this warns loudly
        instead of raising and keeps startup alive; the warning is the only
        signal that the concurrency guarantee is not being met.
        """
        row = conn.execute(f"PRAGMA journal_mode={JOURNAL_MODE.upper()}").fetchone()
        mode = (row[0] if row else "") or ""
        if mode.lower() != JOURNAL_MODE:
            logger.warning(
                f"SQLite did not enable WAL for {self.path}: "
                f"journal_mode returned {mode!r} instead of {JOURNAL_MODE!r}. "
                "Concurrent readers may block on open writer transactions."
            )
        return mode

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Get a thread-safe database connection context manager.

        The connection is closed even when applying the pragmas raises, so a
        failing connection can never be left open.
        
        Yields:
            sqlite3.Connection: Database connection with Row factory enabled.
        """
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        try:
            # Inside the try block on purpose: a pragma that raises (a corrupt
            # file gives ``file is not a database``) must not leave the
            # connection open. It is not logged here; callers that recover,
            # such as ``_initialize_schema()``, do their own reporting.
            conn.row_factory = sqlite3.Row
            self._apply_pragmas(conn)
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
