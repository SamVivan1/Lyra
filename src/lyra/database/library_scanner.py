"""
Library scanning and filesystem indexing.

Scans music library directory for audio files and indexes metadata into local_library.
"""

import logging
import os
import glob
from pathlib import Path
from typing import Optional, Dict, Any

from mutagen import File as MutagenFile

from .connection import DatabaseConnection
from .state import StateManager

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = ('.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav')


class LibraryScanner:
    """
    Scans filesystem for audio files and maintains local library cache.
    
    Responsibilities:
    - Scan music library directory
    - Extract metadata from audio files
    - Index tracks into local_library
    - Support force-rescan operations
    """

    def __init__(
        self,
        library_path: str,
        db_connection: Optional[DatabaseConnection] = None
    ) -> None:
        """
        Initialize LibraryScanner.
        
        Args:
            library_path: Path to music library directory
            db_connection: DatabaseConnection instance. Creates new if None.
        """
        self.library_path = Path(library_path)
        self.db = db_connection or DatabaseConnection()
        self.state_manager = StateManager(self.db)

    def scan_library(self, force: bool = False) -> int:
        """
        Scan music library and index into local_library.
        
        Args:
            force: If True, force a full rescan by clearing cache first
            
        Returns:
            Number of tracks added during this scan
        """
        if force:
            logger.info("Force rescan requested - clearing local library cache")
            self.state_manager.clear_local_library()
        elif self.state_manager.local_library_populated():
            logger.info("Local library cache already populated, skipping scan")
            return 0

        logger.info(f"Scanning music library at {self.library_path}")
        if not self.library_path.exists():
            logger.warning(f"Library path does not exist: {self.library_path}")
            return 0

        files = glob.glob(
            os.path.join(self.library_path, '**', '*'),
            recursive=True
        )
        added = 0

        for file_path in files:
            if not os.path.isfile(file_path):
                continue
            
            if os.path.splitext(file_path)[1].lower() not in AUDIO_EXTENSIONS:
                continue

            metadata = self._extract_metadata(file_path)
            if not metadata['artist'] or not metadata['title']:
                logger.debug(f"Skipping file without essential metadata: {file_path}")
                continue

            if self.state_manager.add_local_track(
                metadata['artist'],
                metadata['title'],
                album=metadata['album'],
                recording_mbid=metadata['recording_mbid'],
                file_path=file_path
            ):
                added += 1

        logger.info(f"Library scan completed: {added} tracks indexed")
        return added

    def _extract_metadata(self, file_path: str) -> Dict[str, Optional[str]]:
        """
        Extract metadata from audio file using tags and filesystem path.
        
        Args:
            file_path: Path to audio file
            
        Returns:
            Dictionary with artist, title, album, recording_mbid
        """
        metadata = {
            'artist': None,
            'title': None,
            'album': None,
            'recording_mbid': None
        }

        # Try to extract from ID3 tags
        try:
            audio = MutagenFile(file_path, easy=True)
            if audio is not None:
                metadata['artist'] = self._first_tag(audio.get('artist'))
                metadata['title'] = self._first_tag(audio.get('title'))
                metadata['album'] = self._first_tag(audio.get('album'))
                metadata['recording_mbid'] = self._first_tag(
                    audio.get('musicbrainz_recordingid')
                )
        except Exception as e:
            logger.debug(f"Failed to read ID3 tags from {file_path}: {e}")

        # Fallback: infer from path if tags missing
        if not metadata['artist'] or not metadata['title']:
            inferred = self._infer_metadata_from_path(file_path)
            metadata['artist'] = metadata['artist'] or inferred['artist']
            metadata['album'] = metadata['album'] or inferred['album']
            metadata['title'] = metadata['title'] or inferred['title']

        return metadata

    @staticmethod
    def _first_tag(value: Any) -> Optional[str]:
        """
        Extract first value from tag (which may be list or string).
        
        Args:
            value: Tag value (list, string, or None)
            
        Returns:
            First string value or None
        """
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, str):
            return value
        return None

    def _infer_metadata_from_path(self, file_path: str) -> Dict[str, Optional[str]]:
        """
        Infer metadata from file path structure.
        
        Expected structure: Library/Artist/Album/Title.ext
        
        Args:
            file_path: Path to audio file
            
        Returns:
            Dictionary with inferred artist, album, title
        """
        metadata = {'artist': None, 'album': None, 'title': None}
        rel_path = os.path.relpath(file_path, self.library_path)
        parts = rel_path.replace('\\', '/').split('/')
        
        if len(parts) >= 3:
            metadata['artist'] = parts[0]
            metadata['album'] = parts[1]
            metadata['title'] = os.path.splitext(parts[-1])[0]
        elif len(parts) == 2:
            metadata['artist'] = parts[0]
            metadata['title'] = os.path.splitext(parts[-1])[0]
        else:
            metadata['title'] = os.path.splitext(parts[-1])[0]
        
        return metadata
