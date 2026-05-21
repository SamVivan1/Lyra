"""
Track state management for fallback orchestration.

Manages tracked_tracks table for per-track fallback status and completion.
"""

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from .connection import DatabaseConnection

logger = logging.getLogger(__name__)


class TrackStateManager:
    """
    Manages individual track fallback state and completion tracking.
    
    Responsibilities:
    - Add and query tracked tracks
    - Mark tracks as completed
    - Retrieve pending tracks for fallback
    """

    def __init__(self, db_connection: Optional[DatabaseConnection] = None) -> None:
        """
        Initialize TrackStateManager.
        
        Args:
            db_connection: DatabaseConnection instance. Creates new if None.
        """
        self.db = db_connection or DatabaseConnection()

    @staticmethod
    def _now_iso() -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    def _tracked_track_exists(
        self,
        recording_mbid: Optional[str],
        artist: str,
        title: str
    ) -> bool:
        """
        Check if a track is already being tracked.
        
        Args:
            recording_mbid: MusicBrainz recording ID (can be None)
            artist: Artist name
            title: Track title
            
        Returns:
            True if track exists in tracked_tracks, False otherwise
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                if recording_mbid:
                    cursor.execute(
                        'SELECT 1 FROM tracked_tracks WHERE recording_mbid = ? LIMIT 1',
                        (recording_mbid,)
                    )
                    if cursor.fetchone():
                        return True
                
                cursor.execute('''
                    SELECT 1 FROM tracked_tracks 
                    WHERE artist = ? COLLATE NOCASE AND title = ? COLLATE NOCASE 
                    LIMIT 1
                ''', (artist, title))
                return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Failed to check tracked track existence: {e}")
            return False

    def add_tracked_track(
        self,
        recording_mbid: Optional[str],
        artist: str,
        title: str,
        source: str = 'listenbrainz'
    ) -> bool:
        """
        Add a track to the tracked_tracks table.
        
        Prevents duplicates by checking if track already exists.
        
        Args:
            recording_mbid: Optional MusicBrainz recording ID
            artist: Artist name
            title: Track title
        Returns:
            True if successfully added, False if already exists
        """
        if self._tracked_track_exists(recording_mbid, artist, title):
            logger.debug(f"Track '{artist} - {title}' already being tracked")
            return False

        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                now = self._now_iso()
                cursor.execute('''
                    INSERT INTO tracked_tracks 
                    (recording_mbid, artist, title, source, status, added_at, updated_at)
                    VALUES (?, ?, ?, ?, 'PENDING', ?, ?)
                ''', (recording_mbid, artist, title, source, now, now))
                conn.commit()
                logger.debug(f"Added tracked track: '{artist} - {title}'")
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            logger.debug(f"Track already exists in tracked_tracks: '{artist} - {title}'")
            return False
        except sqlite3.Error as e:
            logger.error(f"Failed to add tracked track '{artist} - {title}': {e}")
            return False

    def get_pending_tracked_tracks(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get tracked tracks that are pending and older than specified hours.
        
        Args:
            hours: Number of hours to check against (default: 24)
            
        Returns:
            List of pending track dictionaries with id, recording_mbid, artist, title, source, added_at
        """
        pending_tracks: List[Dict[str, Any]] = []
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, recording_mbid, artist, title, source, added_at 
                    FROM tracked_tracks 
                    WHERE status = "PENDING"
                    ORDER BY added_at ASC
                ''')
                rows = cursor.fetchall()

                now = datetime.now(timezone.utc)
                for row in rows:
                    track_id, recording_mbid, artist, title, source, added_at_str = row
                    try:
                        added_at = datetime.fromisoformat(added_at_str)
                        if added_at.tzinfo is None:
                            added_at = added_at.replace(tzinfo=timezone.utc)
                        
                        delta = now - added_at
                        if delta.total_seconds() > hours * 3600:
                            pending_tracks.append({
                                'id': track_id,
                                'recording_mbid': recording_mbid,
                                'artist': artist,
                                'title': title,
                                'source': source,
                                'added_at': added_at_str
                            })
                    except ValueError as e:
                        logger.error(f"Error parsing timestamp for track id={track_id}: {e}")
                
                logger.debug(f"Found {len(pending_tracks)} pending tracks")
                return pending_tracks
        except sqlite3.Error as e:
            logger.error(f"Failed to get pending tracked tracks: {e}")
            return []

    def mark_tracked_track_completed(
        self,
        track_id: Optional[int] = None,
        recording_mbid: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None
    ) -> bool:
        """
        Mark a tracked track as completed.
        
        Args:
            track_id: Primary key ID (prioritized)
            recording_mbid: MusicBrainz recording ID (second priority)
            artist: Artist name (third priority, requires title)
            title: Track title (third priority, requires artist)
            
        Returns:
            True if successfully updated, False otherwise
        """
        if not (track_id or recording_mbid or (artist and title)):
            logger.warning("mark_tracked_track_completed: No identifier provided")
            return False

        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                now = self._now_iso()
                
                if track_id:
                    cursor.execute('''
                        UPDATE tracked_tracks 
                        SET status = "COMPLETED", updated_at = ? 
                        WHERE id = ?
                    ''', (now, track_id))
                elif recording_mbid:
                    cursor.execute('''
                        UPDATE tracked_tracks 
                        SET status = "COMPLETED", updated_at = ? 
                        WHERE recording_mbid = ?
                    ''', (now, recording_mbid))
                else:
                    cursor.execute('''
                        UPDATE tracked_tracks 
                        SET status = "COMPLETED", updated_at = ? 
                        WHERE artist = ? COLLATE NOCASE AND title = ? COLLATE NOCASE
                    ''', (now, artist, title))
                
                conn.commit()
                affected = cursor.rowcount
                if affected > 0:
                    logger.debug(f"Marked {affected} track(s) as completed")
                return affected > 0
        except sqlite3.Error as e:
            logger.error(f"Failed to mark tracked track as completed: {e}")
            return False
