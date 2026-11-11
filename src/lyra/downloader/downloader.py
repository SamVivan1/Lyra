"""
Core audio downloader using yt-dlp.

Handles YouTube searches and downloads with metadata tagging.
"""

import logging
import subprocess
import os
import json
import re
import glob
from pathlib import Path
from typing import Optional, List, Dict, Any

from .metadata_handler import MetadataHandler
from ..config import Config

logger = logging.getLogger(__name__)


class Downloader:
    """
    Downloads audio files from YouTube using yt-dlp.
    
    Responsibilities:
    - YouTube search
    - Audio download and conversion to MP3
    - Metadata tagging
    - Library organization
    """

    def __init__(self, library_path: Optional[str] = None, dry_run: bool = False):
        """
        Initialize Downloader.

        Args:
            library_path: Path to music library (default: Config.LIBRARY_PATH)
            dry_run: If True, nothing is downloaded, tagged or written; the
                read-only yt-dlp calls still work so a simulation can reach the
                screen that offers the download
        """
        self.library_path = Path(library_path or self._resolve_library_root())
        self.metadata_handler = MetadataHandler()
        self.dry_run = dry_run
        self.last_downloaded_path: Optional[str] = None

    def _resolve_library_root(self) -> str:
        """Resolve the actual library root from configuration."""
        library_paths = Config.library_paths()
        for path in library_paths:
            if os.path.exists(path):
                return path
        return library_paths[0] if library_paths else Config.DOWNLOAD_PATH

    def _track_exists(self, artist: str, track: str) -> bool:
        """
        Check if track already exists in library (simple file search).
        
        Args:
            artist: Artist name
            track: Track name
            
        Returns:
            True if file matching artist/track found
        """
        safe_artist = artist.replace('*', '').replace('?', '')
        safe_track = track.replace('*', '').replace('?', '')

        pattern = os.path.join(self.library_path, safe_artist, "**", f"*{safe_track}*")
        matches = glob.glob(pattern, recursive=True)

        old_pattern = os.path.join(self.library_path, "**", f"*{safe_artist}*{safe_track}*")
        matches.extend(glob.glob(old_pattern, recursive=True))

        return len(matches) > 0

    def download_track(self, artist: str, track: str, dry_run: bool = False) -> bool:
        """
        Download track from YouTube (automated).
        
        Args:
            artist: Artist name
            track: Track name
            dry_run: If True, don't actually download (or-ed with the store flag)
            
        Returns:
            True if successfully downloaded or already exists
        """
        dry_run = dry_run or self.dry_run

        if self._track_exists(artist, track):
            logger.info(f"Track '{artist} - {track}' already exists. Skipping.")
            return True

        if dry_run:
            logger.info(f"[DRY RUN] Would download: '{artist} - {track}'")
            return True

        query = f"ytsearch1:{artist} {track} official audio"
        return self.download_manual(query, artist, track, dry_run=dry_run)

    def _get_yt_dlp_base_cmd(self) -> List[str]:
        """
        Get base yt-dlp command with cookies support if available.
        
        Returns:
            List starting with yt-dlp and optional cookie paths
        """
        cmd = ["yt-dlp"]
        cookies_paths = ["/app/data/cookies.txt", "data/cookies.txt", "cookies.txt"]
        
        for cookies_path in cookies_paths:
            if os.path.exists(cookies_path):
                cmd.extend(["--cookies", cookies_path])
                logger.debug(f"Using cookies from {cookies_path}")
                break
        
        return cmd

    def _safe_name(self, name: str) -> str:
        """
        Sanitize name for filesystem use.
        
        Args:
            name: Original name
            
        Returns:
            Sanitized name
        """
        return re.sub(r'[<>:"/\\|?*\n\r]+', '_', (name or '').strip())

    def _find_downloaded_file(self, output_folder: str, safe_title: str) -> Optional[str]:
        """Find the downloaded mp3 file after yt-dlp finishes."""
        candidates = glob.glob(os.path.join(output_folder, f"{safe_title}*.mp3"))
        if candidates:
            return os.path.abspath(candidates[0])
        return None

    def _find_or_create_artist_folder(self, artist: str) -> str:
        """
        Find existing artist folder (case-insensitive) or create new.
        
        Args:
            artist: Artist name
            
        Returns:
            Path to artist folder
        """
        artist_safe = self._safe_name(artist)
        artist_lower = artist_safe.lower()
        
        self.library_path.mkdir(parents=True, exist_ok=True)
        
        for entry in self.library_path.iterdir():
            if entry.is_dir() and entry.name.lower() == artist_lower:
                return str(entry)
        
        new_folder = self.library_path / artist_safe
        new_folder.mkdir(parents=True, exist_ok=True)
        return str(new_folder)

    def _find_or_create_album_folder(self, artist: str, album: str) -> str:
        """
        Find or create album folder under artist folder.
        
        Args:
            artist: Artist name
            album: Album name
            
        Returns:
            Path to album folder
        """
        album_safe = self._safe_name(album or 'Unknown Album')
        artist_folder = self._find_or_create_artist_folder(artist)
        album_folder = os.path.join(artist_folder, album_safe)
        os.makedirs(album_folder, exist_ok=True)
        return album_folder

    def download_manual(
        self,
        query_or_url: str,
        artist: str,
        title: str,
        album: Optional[str] = None,
        album_artist: Optional[str] = None,
        year: Optional[str] = None,
        release_date: Optional[str] = None,
        track_number: Optional[int] = None,
        dry_run: bool = False
    ) -> bool:
        """
        Download from URL or query with metadata tagging.
        
        Args:
            query_or_url: YouTube URL or search query
            artist: Artist name
            title: Track title
            album: Album name (optional)
            album_artist: Album artist (optional)
            year: Release year (optional)
            release_date: Full release date (optional)
            track_number: Track number in album (optional)
            dry_run: If True, don't actually download (or-ed with the store flag)
            
        Returns:
            True if successful, False otherwise
        """
        dry_run = dry_run or self.dry_run

        if self._track_exists(artist, title):
            logger.info(f"Track '{artist} - {title}' already exists. Skipping.")
            return True

        if dry_run:
            logger.info(f"[DRY RUN] Would download: '{artist} - {title}'")
            return True

        if album:
            output_folder = self._find_or_create_album_folder(artist, album)
        else:
            output_folder = self._find_or_create_artist_folder(artist)
        
        safe_title = self._safe_name(title)
        output_template = os.path.join(output_folder, f"{safe_title}.%(ext)s")
        expected_output_path = os.path.join(output_folder, f"{safe_title}.mp3")

        command = self._get_yt_dlp_base_cmd() + [
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--embed-metadata",
            "--embed-thumbnail",
            "--output", output_template,
            query_or_url
        ]
        
        logger.info(f"Downloading '{artist} - {title}' into {output_folder}")
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True
            )
            logger.debug(f"yt-dlp completed: {result.stdout}")
            
            self.metadata_handler.apply_metadata(
                expected_output_path,
                artist,
                title,
                album=album,
                album_artist=album_artist,
                year=year,
                release_date=release_date,
                track_number=track_number
            )

            actual_path = self._find_downloaded_file(output_folder, safe_title)
            if actual_path:
                self.last_downloaded_path = actual_path
                logger.info(f"Successfully downloaded '{artist} - {title}' to {actual_path}")
            else:
                self.last_downloaded_path = None
                logger.info(f"Successfully downloaded '{artist} - {title}'")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"yt-dlp failed: {e.stderr}")
            return False
        except FileNotFoundError:
            logger.error("yt-dlp not found. Please install: pip install yt-dlp")
            return False

    def get_search_results(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search YouTube for query.
        
        Args:
            query: Search query
            limit: Maximum results (default: 5)
            
        Returns:
            List of search result dictionaries
        """
        command = self._get_yt_dlp_base_cmd() + [
            "--dump-json",
            "--flat-playlist",
            f"ytsearch{limit}:{query}"
        ]
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False
            )
            results = []
            
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    
                    url = data.get('webpage_url') or data.get('url', '')
                    if not url.startswith('http'):
                        url = f"https://www.youtube.com/watch?v={url}" if url else ''
                    
                    results.append({
                        'title': data.get('title', 'Unknown Title'),
                        'uploader': data.get('uploader') or data.get('channel', 'Unknown'),
                        'duration': data.get('duration_string') or str(data.get('duration', 'N/A')),
                        'url': url
                    })
                except json.JSONDecodeError:
                    continue
            
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    def get_video_info(self, url: str) -> Dict[str, Any]:
        """Get metadata for a video or URL using yt-dlp."""
        command = self._get_yt_dlp_base_cmd() + [
            "--skip-download",
            "--dump-json",
            url
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False
            )
            if not result.stdout:
                return {}

            line = result.stdout.strip().splitlines()[0]
            data = json.loads(line)
            return {
                "title": data.get("title", ""),
                "uploader": data.get("uploader") or data.get("channel", ""),
                "duration": data.get("duration_string") or str(data.get("duration", "N/A")),
                "url": data.get("webpage_url") or data.get("url", url)
            }
        except Exception as e:
            logger.error(f"Failed to inspect video metadata: {e}")
            return {}
