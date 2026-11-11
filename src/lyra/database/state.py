"""
Core state management for library and artist tracking.

Refactored StateManager that coordinates local library and artist tracking.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from .connection import DatabaseConnection

logger = logging.getLogger(__name__)


class StateManager:
    """
    Core state manager for local library and artist tracking.
    
    Responsibilities:
    - Manage local library cache
    - Track artists and their recommended tracks
    - Coordinate database operations
    """

    def __init__(
        self,
        db_connection: Optional[DatabaseConnection] = None,
        dry_run: bool = False
    ) -> None:
        """
        Initialize StateManager.

        The dry-run flag lives here, on the object that owns the writes, so that
        no caller can forget to check it: with ``dry_run`` set, every mutating
        method reports success and writes nothing.

        Args:
            db_connection: DatabaseConnection instance. Creates new if None.
            dry_run: If True, no write reaches the database
        """
        self.db = db_connection or DatabaseConnection()
        self.dry_run = dry_run

    @staticmethod
    def _now_iso() -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    # ==================== Local Library Operations ====================

    def add_local_track(
        self,
        artist: str,
        title: str,
        album: Optional[str] = None,
        recording_mbid: Optional[str] = None,
        file_path: Optional[str] = None,
        added_at: Optional[str] = None
    ) -> bool:
        """
        Record a track in the local library cache, refreshing an existing row.

        This is an upsert on the natural key the schema keeps,
        ``(artist COLLATE NOCASE, title COLLATE NOCASE)`` (design §7.3). The
        previous ``INSERT OR IGNORE`` never rewrote a row, so a file that was
        moved or renamed kept its old ``file_path`` forever, and an ``album``
        that was NULL at the first scan stayed NULL forever.

        What the conflict update refreshes, and what it must not:

        * ``file_path`` follows the new scan, which is the whole point: the row
          has to point at where the file is now.
        * ``album`` is only filled in when the new scan knows one, so a scan that
          could not read tags does not erase what an earlier one found.
        * ``recording_mbid`` is kept when the row already has one and filled from
          the new scan otherwise (it is an attribute, not part of the key).
        * ``added_at`` is deliberately *not* touched. It is the age every
          age-based cutoff measures, so rewriting it on every rescan would make
          a track perpetually young and reset its waiting time.

        The conflict target repeats the index's collations, because a target has
        to name that index; ``COLLATE NOCASE`` is what makes ``Artist`` and
        ``artist`` the same row here, exactly as it does in the index.

        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional)
            recording_mbid: MusicBrainz recording ID (optional)
            file_path: Full path to audio file (optional)
            added_at: Timestamp in ISO format (optional, defaults to now)

        Returns:
            True if a new row was inserted, False if an existing row was
            refreshed (or the write failed)
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Not adding local track '{artist} - {title}'")
            return True

        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                now = added_at or self._now_iso()
                # This read exists only for the return value: the row's identity
                # is the unique index's job, and the upsert below is what makes
                # the write idempotent. RETURNING would report which branch ran
                # without it, but that needs SQLite 3.35+, which is newer than
                # the floor this project's migrations deliberately respect.
                already_present = cursor.execute('''
                    SELECT 1 FROM local_library
                    WHERE artist = ? COLLATE NOCASE AND title = ? COLLATE NOCASE
                    LIMIT 1
                ''', (artist, title)).fetchone() is not None
                cursor.execute('''
                    INSERT INTO local_library
                        (artist, album, title, recording_mbid, file_path, added_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(artist COLLATE NOCASE, title COLLATE NOCASE)
                    DO UPDATE SET
                        file_path = excluded.file_path,
                        album = COALESCE(excluded.album, local_library.album),
                        recording_mbid = COALESCE(
                            local_library.recording_mbid, excluded.recording_mbid
                        )
                ''', (artist, album, title, recording_mbid, file_path, now))
                conn.commit()
                return not already_present
        except sqlite3.Error as e:
            logger.error(f"Failed to add local track '{artist} - {title}': {e}")
            return False

    def track_exists(
        self,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        recording_mbid: Optional[str] = None
    ) -> bool:
        """
        Check if a track exists in local library.
        
        Args:
            artist: Artist name (optional)
            title: Track title (optional)
            recording_mbid: MusicBrainz recording ID (optional)
            
        Returns:
            True if track exists, False otherwise
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Check by MBID first if available
                if recording_mbid:
                    cursor.execute(
                        'SELECT 1 FROM local_library WHERE recording_mbid = ? LIMIT 1',
                        (recording_mbid,)
                    )
                    if cursor.fetchone():
                        return True
                
                # Check by artist and title
                if artist and title:
                    cursor.execute('''
                        SELECT 1 FROM local_library 
                        WHERE artist = ? COLLATE NOCASE AND title = ? COLLATE NOCASE 
                        LIMIT 1
                    ''', (artist, title))
                    return cursor.fetchone() is not None
                
                return False
        except sqlite3.Error as e:
            logger.error(f"Failed to check track existence: {e}")
            return False

    def local_library_populated(self) -> bool:
        """
        Check if local library has been indexed.
        
        Returns:
            True if local_library table has entries, False otherwise
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(1) FROM local_library')
                count = cursor.fetchone()[0]
                return count > 0
        except sqlite3.Error as e:
            logger.error(f"Failed to check local library population: {e}")
            return False

    def clear_local_library(self) -> bool:
        """
        Clear all entries from local_library table.
        
        Used when performing a force rescan.
        
        Returns:
            True if successfully cleared
        """
        if self.dry_run:
            logger.info("[DRY RUN] Not clearing the local_library cache")
            return True

        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM local_library')
                conn.commit()
                logger.info("Cleared local_library cache")
                return True
        except sqlite3.Error as e:
            logger.error(f"Failed to clear local library: {e}")
            return False

    # ==================== Artist Tracking Operations ====================

    def add_artist(
        self,
        artist_name: str,
        recommended_tracks: Optional[List[str]] = None
    ) -> bool:
        """
        Add an artist to tracking state.
        
        Args:
            artist_name: Name of artist
            recommended_tracks: List of recommended track titles
            
        Returns:
            True if successfully added, False if already exists
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Not adding artist '{artist_name}'")
            return True

        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                now = self._now_iso()
                tracks_json = json.dumps(recommended_tracks or [])
                cursor.execute('''
                    INSERT OR IGNORE INTO artists 
                    (artist_name, added_at, recommended_tracks)
                    VALUES (?, ?, ?)
                ''', (artist_name, now, tracks_json))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Failed to add artist '{artist_name}': {e}")
            return False

    def get_tracked_artists(self) -> List[str]:
        """
        Get all tracked artist names.
        
        Returns:
            List of artist names
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT artist_name FROM artists")
                return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Failed to get tracked artists: {e}")
            return []

    def ensure_library_indexed(self, force_rescan: bool = False) -> None:
        """
        Ensure library is indexed. Import LibraryScanner locally to avoid circular imports.

        The scanner builds its own StateManager, so the dry-run flag has to be
        handed to it here: a scanner left to its own devices writes through a
        store that was never told about the dry run, which is exactly how a
        ``--rescan --dry-run`` used to fill local_library.

        Args:
            force_rescan: Force full rescan if True
        """
        from ..config import Config
        from .library_scanner import LibraryScanner

        if self.dry_run:
            logger.info("[DRY RUN] Library index will not be written")

        if force_rescan:
            # A no-op in a dry run: the clear goes through this store.
            self.clear_local_library()

        for library_path in Config.library_paths():
            scanner = LibraryScanner(library_path, self.db, dry_run=self.dry_run)
            scanner.scan_library(force=False)
