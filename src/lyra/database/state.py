"""
Core state management for library and artist tracking.

Refactored StateManager that coordinates local library and artist tracking.
"""

import logging
import sqlite3
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

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

    def __init__(self, db_connection: Optional[DatabaseConnection] = None) -> None:
        """
        Initialize StateManager.
        
        Args:
            db_connection: DatabaseConnection instance. Creates new if None.
        """
        self.db = db_connection or DatabaseConnection()

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
        Add a track to the local library cache.
        
        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional)
            recording_mbid: MusicBrainz recording ID (optional)
            file_path: Full path to audio file (optional)
            added_at: Timestamp in ISO format (optional, defaults to now)
            
        Returns:
            True if successfully added, False if already exists
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                now = added_at or self._now_iso()
                cursor.execute('''
                    INSERT OR IGNORE INTO local_library 
                    (artist, album, title, recording_mbid, file_path, added_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (artist, album, title, recording_mbid, file_path, now))
                conn.commit()
                return cursor.rowcount > 0
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
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                now = self._now_iso()
                tracks_json = json.dumps(recommended_tracks or [])
                cursor.execute('''
                    INSERT OR IGNORE INTO artists 
                    (artist_name, added_at, fallback_done, recommended_tracks)
                    VALUES (?, ?, 0, ?)
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

    def get_pending_fallback_artists(
        self,
        hours: int = 24
    ) -> List[Dict[str, Any]]:
        """
        Get artists pending fallback (older than specified hours).
        
        Args:
            hours: Hours threshold (default: 24)
            
        Returns:
            List of artist dictionaries with name and recommended_tracks
        """
        pending_artists: List[Dict[str, Any]] = []
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT artist_name, added_at, recommended_tracks 
                    FROM artists 
                    WHERE fallback_done = 0
                ''')
                rows = cursor.fetchall()
                
                now = datetime.now(timezone.utc)
                for artist_name, added_at_str, recommended_tracks_json in rows:
                    try:
                        added_at = datetime.fromisoformat(added_at_str)
                        if added_at.tzinfo is None:
                            added_at = added_at.replace(tzinfo=timezone.utc)
                        
                        delta = now - added_at
                        if delta.total_seconds() > hours * 3600:
                            try:
                                tracks = json.loads(recommended_tracks_json) if recommended_tracks_json else []
                            except json.JSONDecodeError:
                                tracks = []
                            
                            pending_artists.append({
                                "name": artist_name,
                                "recommended_tracks": tracks
                            })
                    except ValueError as e:
                        logger.error(f"Error parsing timestamp for artist '{artist_name}': {e}")
                
                return pending_artists
        except sqlite3.Error as e:
            logger.error(f"Failed to get pending fallback artists: {e}")
            return []

    def mark_fallback_done(self, artist_name: str) -> bool:
        """
        Mark an artist's fallback as completed.
        
        Args:
            artist_name: Name of artist
            
        Returns:
            True if successfully updated
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE artists SET fallback_done = 1 WHERE artist_name = ?",
                    (artist_name,)
                )
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Failed to mark fallback done for '{artist_name}': {e}")
            return False

    def ensure_library_indexed(self, force_rescan: bool = False) -> None:
        """
        Ensure library is indexed. Import LibraryScanner locally to avoid circular imports.
        
        Args:
            force_rescan: Force full rescan if True
        """
        from .library_scanner import LibraryScanner
        from ..config import Config

        if force_rescan:
            self.clear_local_library()

        for library_path in Config.library_paths():
            scanner = LibraryScanner(library_path, self.db)
            scanner.scan_library(force=False)
