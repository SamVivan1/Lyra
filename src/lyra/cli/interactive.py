"""
Interactive downloader CLI.

Provides interactive menu for manual track downloads and metadata entry.
"""

import logging
import sys
import os
from typing import Optional, Tuple

try:
    from rich.console import Console
    from rich.panel import Panel
    import questionary
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

    def __init__(self):
        """Initialize InteractiveDownloader."""
        if HAS_RICH:
            self.console = Console()
        else:
            self.console = None
        
        self.downloader = Downloader()
        self.musicbrainz_client = MusicBrainzClient()
        self.db_integration = CLIDatabaseIntegration()

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

    def run(self) -> None:
        """Run the interactive downloader menu."""
        if not HAS_RICH:
            self._print("Warning: Install 'rich' for better CLI experience: pip install rich")
        
        while True:
            self._clear_screen()
            self._print("\n[bold cyan]🎵 Lyra Manual Downloader[/bold cyan]" if HAS_RICH else "\n=== Lyra Manual Downloader ===")
            
            try:
                if HAS_RICH:
                    choice = questionary.select(
                        "Select action:",
                        choices=[
                            "🔍 Search YouTube",
                            "🔗 Download from URL",
                            "❌ Exit"
                        ]
                    ).ask()
                else:
                    self._print("\n1. Search YouTube")
                    self._print("2. Download from URL")
                    self._print("3. Exit")
                    choice = input("\nSelect (1-3): ").strip()
                
                if choice == "🔍 Search YouTube" or choice == "1":
                    self._search_and_download()
                elif choice == "🔗 Download from URL" or choice == "2":
                    self._download_from_url()
                elif choice == "❌ Exit" or choice == "3" or choice is None:
                    self._print("[yellow]Goodbye![/yellow]" if HAS_RICH else "Goodbye!")
                    break
            except KeyboardInterrupt:
                self._print("\n[yellow]Cancelled[/yellow]" if HAS_RICH else "\nCancelled")
                break
            except Exception as e:
                logger.error(f"Error in interactive mode: {e}")
                self._print(f"[red]Error: {e}[/red]" if HAS_RICH else f"Error: {e}")
                input("Press Enter to continue...")

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

        self._print("\n[cyan]Search Results:[/cyan]" if HAS_RICH else "\nSearch Results:")
        for i, result in enumerate(results, 1):
            self._print(
                f"{i}. [{result['duration']}] {result['title']} "
                f"(by {result['uploader']})"
            )
        
        try:
            choice = int(input("\nSelect (1-5) or 0 to cancel: ").strip())
            if choice < 1 or choice > len(results):
                return
            
            selected = results[choice - 1]
            self._perform_download(selected['url'], selected['title'], selected['uploader'])
        except (ValueError, IndexError):
            self._print("[red]Invalid selection[/red]" if HAS_RICH else "Invalid selection")
            input("Press Enter to continue...")

    def _download_from_url(self) -> None:
        """Download from user-provided URL."""
        url = input("\nEnter URL (YouTube, Spotify, etc.): ").strip()
        if not url:
            return
        
        self._perform_download(url, "", "")

    def _perform_download(
        self,
        url: str,
        default_title: str = "",
        default_uploader: str = ""
    ) -> None:
        """
        Perform the actual download with metadata input.
        
        Args:
            url: Download URL
            default_title: Default video title
            default_uploader: Default uploader name
        """
        artist = input("\nArtist name: ").strip() or default_uploader or "Unknown Artist"
        title = input("Track title: ").strip() or default_title or "Unknown Title"
        album = input("Album (optional): ").strip() or None
        
        self._print("[green]Downloading...[/green]" if HAS_RICH else "Downloading...")
        
        success = self.downloader.download_manual(
            url,
            artist,
            title,
            album=album
        )
        
        if success:
            self.db_integration.record_manual_download(artist, title, album=album)
            self._print(
                "[bold green]✓ Download successful![/bold green]" if HAS_RICH else "\n✓ Download successful!"
            )
        else:
            self._print(
                "[bold red]✗ Download failed[/bold red]" if HAS_RICH else "\n✗ Download failed"
            )
        
        input("\nPress Enter to continue...")
