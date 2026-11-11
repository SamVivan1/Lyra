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

    def add_tracked_track(
        self,
        recording_mbid: Optional[str],
        artist: str,
        title: str,
        source: str = 'listenbrainz'
    ) -> bool:
        """
        Add a track to the tracked_tracks table, once.

        Duplicate detection is the statement's job, not a separate read: the
        unique index on ``artist COLLATE NOCASE, title COLLATE NOCASE`` decides,
        so two processes inserting the same track at the same time cannot both
        believe the track was absent. A row already in the table is left exactly
        as it is -- it may be mid-retry, and re-adding it must not reset that.

        Args:
            recording_mbid: Optional MusicBrainz recording ID (an attribute, not
                part of the key: the same recording id does not make two
                different artist + title pairs the same track)
            artist: Artist name
            title: Track title
            source: Where the track came from ('listenbrainz' or 'manual')

        Returns:
            True if the row was inserted, False if the track was already tracked
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                now = self._now_iso()
                cursor.execute('''
                    INSERT INTO tracked_tracks
                    (recording_mbid, artist, title, source, status, added_at, updated_at)
                    VALUES (?, ?, ?, ?, 'NEW', ?, ?)
                    ON CONFLICT DO NOTHING
                ''', (recording_mbid, artist, title, source, now, now))
                conn.commit()
                inserted = cursor.rowcount > 0
        except sqlite3.IntegrityError:
            logger.debug(f"Track failed the constraints of tracked_tracks: '{artist} - {title}'")
            return False
        except sqlite3.Error as e:
            logger.error(f"Failed to add tracked track '{artist} - {title}': {e}")
            return False
        if inserted:
            logger.debug(f"Added tracked track: '{artist} - {title}'")
        else:
            logger.debug(f"Track '{artist} - {title}' already being tracked")
        return inserted

    def get_pending_tracked_tracks(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get tracked tracks that are new (queued) and older than specified hours.
        
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
                    WHERE status = 'NEW'
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

        The identifiers are tried in this order, and the natural key comes before
        the MBID on purpose: two rows may legitimately share one ``recording_mbid``
        (the unique index is on artist + title, and an MBID is an attribute), so a
        caller that supplies artist + title must not have the other row completed
        as well. The MBID is the fallback for the callers that have nothing else.

        Args:
            track_id: Primary key ID (prioritized)
            artist: Artist name (second priority, requires title)
            title: Track title (second priority, requires artist)
            recording_mbid: MusicBrainz recording ID (third priority: it can be
                shared by several tracked tracks)
            
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
                elif artist and title:
                    cursor.execute('''
                        UPDATE tracked_tracks 
                        SET status = "COMPLETED", updated_at = ? 
                        WHERE artist = ? COLLATE NOCASE AND title = ? COLLATE NOCASE
                    ''', (now, artist, title))
                else:
                    cursor.execute('''
                        UPDATE tracked_tracks 
                        SET status = "COMPLETED", updated_at = ? 
                        WHERE recording_mbid = ?
                    ''', (now, recording_mbid))
                
                conn.commit()
                affected = cursor.rowcount
                if affected > 0:
                    logger.debug(f"Marked {affected} track(s) as completed")
                return affected > 0
        except sqlite3.Error as e:
            logger.error(f"Failed to mark tracked track as completed: {e}")
            return False
