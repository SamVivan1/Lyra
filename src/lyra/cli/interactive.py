"""
Interactive downloader CLI.

Provides interactive menu for manual track downloads and metadata entry.
"""

import logging
import os
import sys
import termios
import tty
from typing import Any, Dict, List, Optional, Tuple

try:
    from rich.console import Console
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from ..api import MusicBrainzClient
from ..downloader import Downloader
from .db_integration import CLIDatabaseIntegration

logger = logging.getLogger(__name__)


class InteractiveDownloader:
    """
    Interactive CLI for manual track downloads.
    
    Provides menu-driven interface for:
    - YouTube search and download
    - Direct URL download
    - Metadata entry
    - Database integration
    """

    def __init__(
        self,
        downloader: Optional[Downloader] = None,
        db_integration: Optional[CLIDatabaseIntegration] = None,
        console: Optional[Any] = None,
        dry_run: bool = False
    ):
        """
        Initialize InteractiveDownloader.

        Every collaborator that touches the network or the database can be
        injected, so a test exercises the real class without either. The
        arguments are keyword-friendly on purpose: the CLI passes the database
        integration it has already initialised.

        Args:
            downloader: Downloader instance. Creates one if None, carrying the
                dry-run flag so a simulation reaches no subprocess.
            db_integration: CLIDatabaseIntegration instance. Creates one if None.
            console: Rich console. Creates one if None and ``rich`` is available.
            dry_run: If True, nothing is downloaded, tagged or recorded
        """
        self.dry_run = dry_run

        if console is not None:
            self.console = console
        elif HAS_RICH:
            self.console = Console()
        else:
            self.console = None

        self.downloader = (
            downloader if downloader is not None else Downloader(dry_run=dry_run)
        )
        self.musicbrainz_client = MusicBrainzClient()
        self.db_integration = (
            db_integration if db_integration is not None
            else CLIDatabaseIntegration(dry_run=dry_run)
        )

    def _print(self, message: str, style: Optional[str] = None) -> None:
        """Print message with optional styling."""
        if self.console and HAS_RICH:
            self.console.print(message, style=style)
        else:
            print(message)

    def _clear_screen(self) -> None:
        """Clear console screen."""
        os.system('cls' if os.name == 'nt' else 'clear')

    def _parse_artist_title_from_label(
        self,
        label: str,
        uploader: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse artist and title from YouTube video title.
        
        Args:
            label: Video title
            uploader: Video uploader name (optional)
            
        Returns:
            Tuple of (artist, title) or (None, None)
        """
        label = label.strip()
        for delim in [" - ", " – ", " — "]:
            if delim in label:
                parts = [part.strip() for part in label.split(delim, 1)]
                if len(parts) == 2:
                    left = self._clean_label_text(parts[0])
                    right = self._clean_label_text(parts[1])
                    if uploader and uploader.lower() != left.lower():
                        return left, right
                    return left, right
        return None, None

    @staticmethod
    def _clean_label_text(text: str) -> str:
        """Remove common suffixes from video title."""
        if not text:
            return text
        
        cleaned = text.strip()
        while '(' in cleaned and ')' in cleaned:
            start = cleaned.index('(')
            end = cleaned.index(')', start)
            cleaned = (cleaned[:start] + cleaned[end + 1:]).strip()
        
        for suffix in [
            'official lyric video',
            'official video',
            'official audio',
            'lyrics',
            'lyric video'
        ]:
            cleaned = cleaned.replace(suffix, '')
        
        return ' '.join(cleaned.split()).strip()

    def _get_single_character(self) -> str:
        """Read a single character from stdin without requiring Enter."""
        if os.name == "nt":
            import msvcrt
            # msvcrt is Windows-only; on other platforms its stubs omit getwch.
            return msvcrt.getwch()  # type: ignore[attr-defined]

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                if seq == "[A":
                    return "k"
                if seq == "[B":
                    return "j"
                if seq == "[C":
                    return "l"
                if seq == "[D":
                    return "h"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _select_with_hjkl(self, items: List[str], title: str) -> Optional[int]:
        """Select an item from a list using hjkl navigation."""
        if not items:
            return None

        selected = 0
        while True:
            self._clear_screen()
            self._print(f"\n{title}\n")
            for index, item in enumerate(items):
                prefix = ">" if index == selected else " "
                self._print(f" {prefix} {item}")

            hint = (
                "\nUse [bold]hjkl[/bold] or arrow keys to navigate, Enter to select, q to cancel."
                if HAS_RICH
                else "\nUse hjkl or arrow keys to navigate, Enter to select, q to cancel."
            )
            self._print(hint)

            char = self._get_single_character()
            if char in {"j", "J", "l", "L"}:
                selected = (selected + 1) % len(items)
            elif char in {"k", "K", "h", "H"}:
                selected = (selected - 1) % len(items)
            elif char in {"\r", "\n"}:
                return selected
            elif char in {"q", "Q", "\x03", "\x1b"}:
                return None

                self._clear_screen()

    def _prompt_field(self, label: str, current_value: Optional[str] = None) -> Optional[str]:
        prompt = f"{label} [{current_value if current_value else 'none'}]: "
        value = input(prompt).strip()
        if value.lower() == "exit":
            sys.exit(0)
        return value if value else current_value

    def _lookup_metadata(self, artist: str, title: str) -> Dict[str, Any]:
        metadata = {
            "artist": artist,
            "title": title,
            "album": None,
            "track_number": None,
            "album_artist": None,
            "year": None,
            "release_date": None
        }

        try:
            mb_data = self.musicbrainz_client.get_best_release_metadata(artist, title)
            if mb_data:
                metadata["artist"] = mb_data.get("album_artist") or metadata["artist"]
                metadata["title"] = mb_data.get("recording_title") or metadata["title"]
                metadata["album"] = mb_data.get("album")
                metadata["track_number"] = mb_data.get("track_position")
                metadata["album_artist"] = mb_data.get("album_artist")
                metadata["year"] = mb_data.get("year")
                metadata["release_date"] = mb_data.get("release_date")
        except Exception as e:
            logger.debug(f"MusicBrainz metadata lookup failed: {e}")

        return metadata

    def _confirm_metadata(self, metadata: Dict[str, Any]) -> bool:
        self._print("\nSuggested metadata:")
        self._print(f"Artist: {metadata.get('artist')}")
        self._print(f"Title: {metadata.get('title')}")
        self._print(f"Album: {metadata.get('album') or 'Unknown'}")
        self._print(f"Track #: {metadata.get('track_number') or 'Unknown'}")
        self._print(f"Release date: {metadata.get('release_date') or 'Unknown'}")

        response = input("\nIs this correct? [y/N]: ").strip().lower()
        if response == "exit":
            sys.exit(0)
        return response == "y"

    def _manual_metadata_entry(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        self._print("\nEnter corrected metadata or type 'exit' to cancel.")
        metadata["artist"] = self._prompt_field("Artist", metadata.get("artist"))
        metadata["title"] = self._prompt_field("Title", metadata.get("title"))
        metadata["album"] = self._prompt_field("Album", metadata.get("album"))
        track_number = self._prompt_field("Track number", str(metadata.get("track_number") or ""))
        if track_number is not None:
            try:
                metadata["track_number"] = int(track_number)
            except ValueError:
                metadata["track_number"] = None
        return metadata

    def _download_with_metadata(self, url: str, metadata: Dict[str, Any]) -> None:
        self._perform_download(
            url,
            metadata.get("artist") or "Unknown Artist",
            metadata.get("title") or "Unknown Title",
            album=metadata.get("album"),
            album_artist=metadata.get("album_artist"),
            year=metadata.get("year"),
            release_date=metadata.get("release_date"),
            track_number=metadata.get("track_number")
        )

    def run(self) -> None:
        """Run the interactive downloader menu."""
        if not HAS_RICH:
            self._print("Warning: Install 'rich' for better CLI experience: pip install rich")

        if self.dry_run:
            # Printed once, before the menu: everything on this screen then
            # looks like a simulation, and the operator can stop expecting files.
            self._print(
                "[bold yellow]DRY RUN[/bold yellow] — nothing will be downloaded, "
                "tagged or recorded."
                if HAS_RICH else
                "DRY RUN — nothing will be downloaded, tagged or recorded."
            )

        try:
            while True:
                actions = [
                    "Search YouTube",
                    "Download from URL",
                    "Exit"
                ]
                selected = self._select_with_hjkl(actions, title="Lyra Manual Downloader")
                if selected is None or selected == 2:
                    self._print("[yellow]Goodbye![/yellow]" if HAS_RICH else "Goodbye!")
                    sys.exit(0)
                if selected == 0:
                    self._search_and_download()
                elif selected == 1:
                    self._download_from_url()
        except KeyboardInterrupt:
            self._print("\n[yellow]Interrupted. Exiting.[/yellow]" if HAS_RICH else "\nInterrupted. Exiting.")
            sys.exit(0)

    def _search_and_download(self) -> None:
        """Interactive YouTube search and download."""
        query = input("\nEnter search query: ").strip()
        if not query:
            return

        self._print("[blue]Searching YouTube...[/blue]" if HAS_RICH else "Searching YouTube...")
        results = self.downloader.get_search_results(query, limit=5)
        
        if not results:
            self._print("[red]No results found[/red]" if HAS_RICH else "No results found")
            input("Press Enter to continue...")
            return

        choices = [f"[{result['duration']}] {result['title']} (by {result['uploader']})" for result in results]
        selected_index = self._select_with_hjkl(choices, title="Search Results")
        if selected_index is None:
            return

        selected = results[selected_index]
        artist, title = self._parse_artist_title_from_label(
            selected["title"], selected.get("uploader")
        )
        if not artist or not title:
            artist = selected.get("uploader") or "Unknown Artist"
            title = selected.get("title") or "Unknown Title"

        metadata = self._lookup_metadata(artist, title)
        if not self._confirm_metadata(metadata):
            metadata = self._manual_metadata_entry(metadata)

        self._download_with_metadata(selected["url"], metadata)

    def _download_from_url(self) -> None:
        """Download from user-provided URL."""
        url = input("\nEnter URL (YouTube, Spotify, etc.): ").strip()
        if not url:
            return

        info = self.downloader.get_video_info(url)
        artist, title = self._parse_artist_title_from_label(
            info.get("title", ""), info.get("uploader", "")
        )
        if not artist or not title:
            artist = info.get("uploader") or "Unknown Artist"
            title = info.get("title") or "Unknown Title"

        metadata = self._lookup_metadata(artist, title)
        if not self._confirm_metadata(metadata):
            metadata = self._manual_metadata_entry(metadata)

        self._download_with_metadata(url, metadata)

    def _perform_download(
        self,
        url: str,
        artist: str,
        title: str,
        album: Optional[str] = None,
        album_artist: Optional[str] = None,
        year: Optional[str] = None,
        release_date: Optional[str] = None,
        track_number: Optional[int] = None
    ) -> None:
        """
        Perform the actual download with metadata.

        Args:
            url: Download URL
            artist: Artist name
            title: Track title
            album: Optional album name
            album_artist: Optional album artist name
            year: Optional release year
            release_date: Optional release date
            track_number: Optional track number
        """
        self._print("[green]Downloading...[/green]" if HAS_RICH else "Downloading...")

        success = self.downloader.download_manual(
            url,
            artist,
            title,
            album=album,
            album_artist=album_artist,
            year=year,
            release_date=release_date,
            track_number=track_number,
            dry_run=self.dry_run
        )

        if success and self.dry_run:
            # ``download_manual`` reports True for a simulated download, and
            # recording that would put a track in the library that is not on
            # disk. Say so plainly instead of claiming a download happened.
            self._print(
                "[bold yellow]DRY RUN[/bold yellow] — nothing was downloaded or recorded."
                if HAS_RICH else "\nDRY RUN — nothing was downloaded or recorded."
            )
        elif success:
            self.db_integration.record_manual_download(artist, title, album=album)
            self._print(
                "[bold green]✓ Download successful![/bold green]" if HAS_RICH else "\n✓ Download successful!"
            )
        else:
            self._print(
                "[bold red]✗ Download failed[/bold red]" if HAS_RICH else "\n✗ Download failed"
            )

        input("\nPress Enter to continue...")
