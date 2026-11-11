"""
Library scanning and filesystem indexing.

Scans music library directory for audio files and indexes metadata into local_library.
"""

import logging
import os
import glob
from pathlib import Path
from typing import Optional, Dict, Any

import mutagen
from mutagen import File as MutagenFile

from .connection import DatabaseConnection
from .state import StateManager

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = ('.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav')

# Tag keys that may carry the MusicBrainz recording MBID, most specific first.
# None of these is exposed through mutagen's EasyID3 view, so the file has to
# be opened with the non-easy API to read them.
RECORDING_MBID_TAG_KEYS = (
    'musicbrainz_trackid',
    'TXXX:MusicBrainz Recording Id',
    'UFID:http://musicbrainz.org',
)


def _coerce_mbid_value(value: Any) -> Optional[str]:
    """
    Normalise a raw tag value into a stripped MBID string.

    Handles the shapes mutagen produces: a UFID frame object carrying the
    payload in ``.data`` as bytes, a TXXX frame object carrying it in ``.text``,
    a list/tuple of values, and a plain string.

    Args:
        value: Raw tag value from the non-easy mutagen API

    Returns:
        Stripped MBID string, or None when there is no usable value
    """
    if hasattr(value, 'data'):
        value = value.data
    elif hasattr(value, 'text'):
        value = value.text
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    if isinstance(value, str):
        return value.strip() or None
    return None


def read_recording_mbid(file_path: str) -> Optional[str]:
    """
    Read the MusicBrainz recording MBID from an audio file.

    Uses the non-easy mutagen API because ``musicbrainz_recordingid`` is not a
    valid EasyID3 key, which silently produced None for every track.

    Args:
        file_path: Path to the audio file

    Returns:
        The recording MBID, or None when absent or unreadable
    """
    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return None

        tags = getattr(audio, 'tags', None)
        if tags is None:
            tags = audio

        for key in RECORDING_MBID_TAG_KEYS:
            mbid = _coerce_mbid_value(tags.get(key))
            if mbid:
                return mbid
        return None
    except Exception as e:
        logger.debug(f"Failed to read recording MBID from {file_path}: {e}")
        return None


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
        db_connection: Optional[DatabaseConnection] = None,
        dry_run: bool = False
    ) -> None:
        """
        Initialize LibraryScanner.

        Args:
            library_path: Path to music library directory
            db_connection: DatabaseConnection instance. Creates new if None.
            dry_run: If True, the scan reads the filesystem and writes no rows
        """
        self.library_path = Path(library_path)
        self.db = db_connection or DatabaseConnection()
        self.dry_run = dry_run
        # The scanner writes through this store, so it must be told the flag.
        self.state_manager = StateManager(self.db, dry_run=dry_run)

    def scan_library(self, force: bool = False) -> int:
        """
        Scan music library and index into local_library.
        
        Args:
            force: If True, force a full rescan by clearing cache first
            
        Returns:
            Number of tracks added during this scan
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Not indexing {self.library_path}")

        if force:
            logger.info("Force rescan requested - clearing local library cache")
            # A no-op in a dry run: the clear goes through the store above.
            self.state_manager.clear_local_library()

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
        except Exception as e:
            logger.debug(f"Failed to read ID3 tags from {file_path}: {e}")

        # The MBID is not visible through the easy API - read it separately.
        metadata['recording_mbid'] = read_recording_mbid(file_path)

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
