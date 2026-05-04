import os
import sys
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from downloader import Downloader
from musicbrainz import MusicBrainzClient
from utils import setup_logger

# Mute standard logger for CLI to avoid spam, we'll use rich instead
logger = setup_logger("cli")
console = Console()
downloader = Downloader()
musicbrainz_client = MusicBrainzClient()

def _prompt_album_metadata(artist, title):
    candidate = musicbrainz_client.get_best_release_metadata(artist, title)
    if candidate and candidate.get("album"):
        album = candidate["album"]
        album_artist = candidate.get("album_artist") or artist
        year = candidate.get("year")
        release_date = candidate.get("release_date")
        track_position = candidate.get("track_position")

        console.print("\n[cyan]Detected album metadata:[/cyan]")
        console.print(f"[bold]Album:[/bold] {album}")
        console.print(f"[bold]Album Artist:[/bold] {album_artist}")
        if release_date:
            console.print(f"[bold]Release Date:[/bold] {release_date}")
        if track_position:
            console.print(f"[bold]Track Number:[/bold] {track_position}")

        use_auto = questionary.confirm("Gunakan metadata ini untuk mengorganisir lagu?", default=True).ask()
        if use_auto:
            return album, album_artist, year, release_date, track_position

    console.print("\n[cyan]Metadata album tidak tersedia atau ditolak. Mohon isi manual.[/cyan]")
    album = questionary.text("Nama Album (untuk folder & metadata):", default=title).ask()
    if not album:
        album = title

    year = questionary.text("Tahun Rilis (opsional):").ask()
    album_artist = questionary.text("Album Artist (opsional, kosong = pakai artis utama):", default=artist).ask()
    if not album_artist:
        album_artist = artist

    track_number_input = questionary.text("Track number dalam album (opsional):").ask()
    try:
        track_number = int(track_number_input) if track_number_input and track_number_input.strip() else None
    except ValueError:
        track_number = None

    release_date = year if year else None
    return album, album_artist, year if year else None, release_date, track_number


def main_menu():
    while True:
        console.clear()
        console.print(Panel.fit("[bold green]🎵 MusicPro Manual Downloader 🎵[/bold green]\nNavigasi menggunakan tombol panah (Up/Down) atau j/k."))
        
        action = questionary.select(
            "Pilih aksi:",
            choices=[
                "🔍 Search Lagu",
                "🔗 Download via Link",
                "❌ Keluar"
            ]
        ).ask()

        if action == "🔍 Search Lagu":
            search_menu()
        elif action == "🔗 Download via Link":
            link_menu()
        elif action == "❌ Keluar" or action is None:
            console.print("[yellow]Sampai jumpa![/yellow]")
            sys.exit(0)

def search_menu():
    query = questionary.text("Masukkan judul lagu / nama artis:").ask()
    if not query:
        return

    with console.status(f"[bold blue]Mencari '{query}' di YouTube..."):
        results = downloader.get_search_results(query, limit=5)
    
    if not results:
        console.print("[red]Tidak ada hasil ditemukan atau terjadi kesalahan.[/red]")
        questionary.press_any_key_to_continue("Tekan apa saja untuk kembali...").ask()
        return

    choices = []
    for idx, r in enumerate(results):
        display_text = f"[{r['duration']}] {r['title']} (by {r['uploader']})"
        choices.append(questionary.Choice(display_text, value=idx))
    
    choices.append(questionary.Choice("⬅️ Kembali", value=-1))

    selected_idx = questionary.select(
        "Pilih lagu untuk didownload (Gunakan j/k untuk navigasi):",
        choices=choices
    ).ask()

    if selected_idx == -1 or selected_idx is None:
        return

    selected_item = results[selected_idx]
    
    console.print(f"\n[cyan]Lagu terpilih:[/cyan] {selected_item['title']}")
    
    artist = questionary.text("Nama Artis (Untuk folder & metadata):", default=selected_item['uploader']).ask()
    title = questionary.text("Judul Lagu (Untuk nama file & metadata):", default=selected_item['title']).ask()

    if not artist or not title:
        console.print("[yellow]Download dibatalkan.[/yellow]")
        return

    album, album_artist, year, release_date, track_number = _prompt_album_metadata(artist, title)

    with console.status(f"[bold green]Mendownload '{artist} - {title}'..."):
        success = downloader.download_manual(
            selected_item['url'],
            artist,
            title,
            album=album,
            album_artist=album_artist,
            year=year,
            release_date=release_date,
            track_number=track_number
        )
    
    if success:
        display_path = os.path.join(downloader.library_path, artist, album or '', f'{title}.mp3')
        console.print(f"[bold green]✅ Berhasil mendownload dan merapikan metadata![/bold green]")
        console.print(f"Path: {display_path}")
    else:
        console.print("[bold red]❌ Gagal mendownload lagu.[/bold red]")
    
    questionary.press_any_key_to_continue("Tekan apa saja untuk kembali ke menu utama...").ask()

def link_menu():
    url = questionary.text("Masukkan URL lagu (YouTube, Spotify, dll):").ask()
    if not url:
        return
        
    artist = questionary.text("Nama Artis (Untuk folder & metadata):").ask()
    title = questionary.text("Judul Lagu (Untuk nama file & metadata):").ask()

    if not artist or not title:
        console.print("[yellow]Download dibatalkan.[/yellow]")
        return

    album, album_artist, year, release_date, track_number = _prompt_album_metadata(artist, title)

    with console.status(f"[bold green]Mendownload '{artist} - {title}'..."):
        success = downloader.download_manual(
            url,
            artist,
            title,
            album=album,
            album_artist=album_artist,
            year=year,
            release_date=release_date,
            track_number=track_number
        )

    if success:
        display_path = os.path.join(downloader.library_path, artist, album or '', f'{title}.mp3')
        console.print(f"[bold green]✅ Berhasil mendownload dan merapikan metadata![/bold green]")
        console.print(f"Path: {display_path}")
    else:
        console.print("[bold red]❌ Gagal mendownload lagu.[/bold red]")

    questionary.press_any_key_to_continue("Tekan apa saja untuk kembali ke menu utama...").ask()

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        console.print("\n[yellow]Membatalkan...[/yellow]")
        sys.exit(0)
