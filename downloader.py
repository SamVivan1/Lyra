import subprocess
import os
import glob
import json
import mutagen
from config import Config
from utils import setup_logger
from mutagen.easyid3 import EasyID3

logger = setup_logger("downloader")

class Downloader:
    def __init__(self, library_path=Config.LIBRARY_PATH):
        self.library_path = library_path

    def _track_exists(self, artist, track):
        """Simple deduplication check: look for similar filenames in the library."""
        safe_artist = artist.replace('*', '').replace('?', '')
        safe_track = track.replace('*', '').replace('?', '')
        
        # Now checks the Artist folder specifically based on the new pattern
        pattern = os.path.join(self.library_path, safe_artist, f"*{safe_track}*")
        matches = glob.glob(pattern)
        
        # Also check the old pattern just in case
        old_pattern = os.path.join(self.library_path, f"*{safe_artist}*{safe_track}*")
        matches.extend(glob.glob(old_pattern))
        
        return len(matches) > 0

    def _apply_metadata(self, file_path, artist, title):
        """Apply ID3 tags to the downloaded MP3 file."""
        if not os.path.exists(file_path):
            return
            
        try:
            audio = EasyID3(file_path)
        except Exception:
            try:
                # If no ID3 tag exists, create one
                m_file = mutagen.File(file_path, easy=True)
                m_file.add_tags()
                audio = m_file
            except Exception as e:
                logger.error(f"Failed to initialize ID3 tags for {file_path}: {e}")
                return

        audio['title'] = title
        audio['artist'] = artist
        audio['albumartist'] = artist
        audio.save()
        logger.debug(f"Applied ID3 tags: Artist='{artist}', Title='{title}' to {file_path}")

    def download_track(self, artist, track):
        """Download track from YouTube using yt-dlp (Automated process)."""
        if self._track_exists(artist, track):
            logger.info(f"Track '{artist} - {track}' already exists in library. Skipping download.")
            return True

        query = f"ytsearch1:{artist} {track} official audio"
        return self.download_manual(query, artist, track)

    def _get_yt_dlp_base_cmd(self):
        """Helper to get base yt-dlp command with cookies if available."""
        cmd = ["yt-dlp"]
        cookies_path = "/app/data/cookies.txt"
        if os.path.exists(cookies_path):
            cmd.extend(["--cookies", cookies_path])
        # Also try local path if running outside docker
        elif os.path.exists("data/cookies.txt"):
            cmd.extend(["--cookies", "data/cookies.txt"])
        elif os.path.exists("cookies.txt"):
            cmd.extend(["--cookies", "cookies.txt"])
        return cmd

    def download_manual(self, query_or_url, artist, title):
        """Download from URL or Search Query, place in Artist/Title.mp3, and apply metadata."""
        artist_folder = os.path.join(self.library_path, artist.replace('/', '_'))
        os.makedirs(artist_folder, exist_ok=True)
        
        # Save file directly as Title.mp3 inside Artist folder
        safe_title = title.replace('/', '_')
        output_template = os.path.join(artist_folder, f"{safe_title}.%(ext)s")
        expected_output_path = os.path.join(artist_folder, f"{safe_title}.mp3")

        command = self._get_yt_dlp_base_cmd() + [
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--embed-metadata",
            "--embed-thumbnail",
            "--output", output_template,
            query_or_url
        ]
        
        logger.info(f"Downloading: '{artist} - {title}'")
        
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            logger.debug(f"yt-dlp output: {result.stdout}")
            
            # Additional ID3 Tagging using Mutagen
            self._apply_metadata(expected_output_path, artist, title)
            
            logger.info(f"Successfully downloaded '{artist} - {title}' to {expected_output_path}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"yt-dlp failed for '{artist} - {title}'. Return code: {e.returncode}")
            logger.error(f"Error output: {e.stderr}")
            return False
        except FileNotFoundError:
            logger.error("yt-dlp not found! Please ensure it is installed and in your PATH.")
            return False

    def get_search_results(self, query, limit=5):
        """Search YouTube and return a list of result dictionaries."""
        command = self._get_yt_dlp_base_cmd() + [
            "--dump-json",
            f"ytsearch{limit}:{query}"
        ]
        
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            results = []
            for line in result.stdout.strip().split('\\n'):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    results.append({
                        'title': data.get('title', 'Unknown Title'),
                        'uploader': data.get('uploader', 'Unknown Uploader'),
                        'duration': data.get('duration_string', 'N/A'),
                        'url': data.get('webpage_url', '')
                    })
                except json.JSONDecodeError:
                    continue
            return results
        except subprocess.CalledProcessError as e:
            logger.error(f"Search failed: {e.stderr}")
            return []
        except FileNotFoundError:
            logger.error("yt-dlp not found! Please ensure it is installed and in your PATH.")
            return []
