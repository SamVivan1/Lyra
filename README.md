# Self-Hosted Music Automation System

A robust, modular Python service for automating music acquisition using a hybrid pipeline in a homelab environment.

## Overview
1. Fetches recommended artists from ListenBrainz.
2. Automatically adds them to Lidarr for torrent-based acquisition.
3. Monitors Lidarr for missing content.
4. Falls back to YouTube (via `yt-dlp`) after 24 hours if content is still missing.
5. Deduplicates and integrates seamlessly with Navidrome via a shared directory.

## Prerequisites
- Lidarr running locally or via network.
- Docker & Docker Compose (optional but recommended).
- A ListenBrainz account with listening history and recommendations.

## Installation (Docker)
1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd music-automation
   ```

2. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and fill in your ListenBrainz username, ListenBrainz API token, Lidarr URL, and Lidarr API Key.

3. Create data directory for SQLite state:
   ```bash
   mkdir data
   ```

4. Build and start the container:
   ```bash
   docker-compose up -d --build
   ```
   The container will run in a continuous loop every 6 hours by default.

## Usage (CLI)

### Interactive Manual Downloader
Terdapat fitur CLI interaktif (`cli.py`) untuk mencari dan mendownload lagu secara manual yang tidak masuk ke otomasi. Fitur ini sudah dilengkapi dengan manajemen metadata otomatis dan sekarang akan mencoba mendeteksi album dari MusicBrainz agar lagu tersimpan dalam folder album yang benar.

Cara termudah untuk menjalankannya jika menggunakan Docker adalah dengan mengeksekusi shell ke dalam container `lyra`:
```bash
docker exec -it lyra python cli.py
```
*(Tip: Anda bisa membuat alias di `~/.zshrc` atau `~/.bashrc` untuk mempermudah, contoh: `alias musicdl="docker exec -it lyra python cli.py"`)*

### Automated Scripts
You can run the script manually or via cron.
First, install dependencies:
```bash
pip install -r requirements.txt
```

Run one-shot operations:
```bash
python main.py
python main.py --dry-run
python main.py --limit 10
python main.py --add-only
python main.py --fallback-only
```

### Example Cron Setup
If you prefer cron instead of the continuous `--loop` mode:
```cron
# Run standard pipeline (add artists + check fallbacks) every 6 hours
0 */6 * * * cd /path/to/music-automation && /usr/bin/python3 main.py

# Alternatively, split the tasks:
# Add artists at 00:00, 12:00
0 0,12 * * * cd /path/to/music-automation && /usr/bin/python3 main.py --add-only
# Check fallback at 06:00, 18:00
0 6,18 * * * cd /path/to/music-automation && /usr/bin/python3 main.py --fallback-only
```

## Volumes & Paths
- By default, Lidarr downloads to `/DATA/Downloads`.
- The final structured library is in `/mnt/Storage2/data/media/Music`.
Ensure these directories exist and are properly mounted in `docker-compose.yml`.

## Logs
Logs are output to the console and saved to `music_automation.log` (or within the Docker container logs).
