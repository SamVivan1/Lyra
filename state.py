import sqlite3
import datetime
import json
from config import Config
from utils import setup_logger

logger = setup_logger("state")

class StateManager:
    def __init__(self, db_path=Config.DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS artists (
                        artist_name TEXT PRIMARY KEY,
                        added_at TIMESTAMP,
                        fallback_done BOOLEAN DEFAULT 0
                    )
                ''')
                
                # Migration: Add recommended_tracks column if it doesn't exist
                cursor.execute("PRAGMA table_info(artists)")
                columns = [info[1] for info in cursor.fetchall()]
                if 'recommended_tracks' not in columns:
                    logger.info("Migrating database: adding 'recommended_tracks' column.")
                    cursor.execute("ALTER TABLE artists ADD COLUMN recommended_tracks TEXT")
                
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize database: {e}")

    def add_artist(self, artist_name, recommended_tracks=None):
        """Add an artist to the tracking state with optional recommended tracks."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                now = datetime.datetime.now(datetime.timezone.utc)
                tracks_json = json.dumps(recommended_tracks or [])
                cursor.execute('''
                    INSERT OR IGNORE INTO artists (artist_name, added_at, fallback_done, recommended_tracks)
                    VALUES (?, ?, 0, ?)
                ''', (artist_name, now, tracks_json))
                conn.commit()
                if cursor.rowcount > 0:
                    logger.debug(f"Added '{artist_name}' to state tracking.")
        except sqlite3.Error as e:
            logger.error(f"Failed to add artist '{artist_name}': {e}")

    def get_tracked_artists(self):
        """Returns a list of all tracked artist names."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT artist_name FROM artists")
                return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Failed to get tracked artists: {e}")
            return []

    def get_pending_fallback_artists(self, hours=Config.FALLBACK_HOURS):
        """Returns artists data that have been tracked for > `hours` and have fallback_done = 0."""
        pending_artists = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT artist_name, added_at, recommended_tracks FROM artists WHERE fallback_done = 0")
                rows = cursor.fetchall()
                
                now = datetime.datetime.now(datetime.timezone.utc)
                for artist_name, added_at_str, recommended_tracks_json in rows:
                    try:
                        added_at = datetime.datetime.fromisoformat(added_at_str)
                        if added_at.tzinfo is None:
                            added_at = added_at.replace(tzinfo=datetime.timezone.utc)
                            
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
                    except Exception as e:
                        logger.error(f"Error parsing timestamp for '{artist_name}': {e}")
                        
            return pending_artists
        except sqlite3.Error as e:
            logger.error(f"Failed to get pending fallback artists: {e}")
            return []

    def mark_fallback_done(self, artist_name):
        """Mark an artist's fallback as done."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE artists SET fallback_done = 1 WHERE artist_name = ?", (artist_name,))
                conn.commit()
                logger.debug(f"Marked fallback as done for '{artist_name}'.")
        except sqlite3.Error as e:
            logger.error(f"Failed to mark fallback done for '{artist_name}': {e}")
