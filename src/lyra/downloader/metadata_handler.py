"""
Metadata tagging utilities for audio files.

Handles ID3 tag writing for MP3 and other audio formats.
"""

import logging
import os
from typing import Optional

from mutagen.easyid3 import EasyID3
import mutagen

logger = logging.getLogger(__name__)


class MetadataHandler:
    """
    Handles metadata tagging for audio files.
    
    Applies ID3 tags for artist, title, album, and other metadata.
    """

    @staticmethod
    def apply_metadata(
        file_path: str,
        artist: str,
        title: str,
        album: Optional[str] = None,
        album_artist: Optional[str] = None,
        year: Optional[str] = None,
        release_date: Optional[str] = None,
        track_number: Optional[int] = None
    ) -> bool:
        """
        Apply metadata tags to audio file.
        
        Args:
            file_path: Path to audio file
            artist: Artist name
            title: Track title
            album: Album name (optional)
            album_artist: Album artist (optional)
            year: Release year (optional)
            release_date: Full release date (optional)
            track_number: Track position in album (optional)
            
        Returns:
            True if successfully applied, False otherwise
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False
        
        try:
            audio = EasyID3(file_path)
        except Exception:
            try:
                m_file = mutagen.File(file_path, easy=True)
                if m_file is None:
                    m_file = mutagen.File(file_path)
                    if m_file is None:
                        logger.error(f"Could not open file for metadata: {file_path}")
                        return False
                    m_file.add_tags()
                audio = m_file
            except Exception as e:
                logger.error(f"Failed to initialize ID3 tags for {file_path}: {e}")
                return False

        try:
            audio['title'] = title
            audio['artist'] = artist
            audio['albumartist'] = album_artist or artist
            
            if album:
                audio['album'] = album
            
            if release_date:
                audio['date'] = release_date
            elif year:
                audio['date'] = year
            
            if track_number:
                audio['tracknumber'] = str(track_number)
            
            audio.save()
            logger.debug(
                f"Applied metadata to {file_path}: "
                f"Artist='{artist}', Title='{title}', Album='{album}'"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to save metadata to {file_path}: {e}")
            return False
