# Lyra: Self-Hosted Music Automation System 🎵

Lyra is a production-ready, modular Python service designed for homelab environments. It automates music acquisition using a hybrid pipeline that prioritizes high-quality torrents via **Lidarr** and falls back to **YouTube** for hard-to-find content.

## 🚀 Key Features

*   **ListenBrainz Integration**: Fetches personalized artist recommendations based on your listening history.
*   **Hybrid Acquisition**: 
    *   **Primary**: Automatically adds artists to Lidarr for high-quality automated downloads.
    *   **Fallback**: If Lidarr fails to acquire content after a configurable delay (default 24h), Lyra automatically downloads top tracks from YouTube.
*   **Intelligent Path Mapping**: Supports decoupled path configurations for Docker-to-Host compatibility.
*   **Manual CLI Downloader**: Interactive search and download tool with automatic metadata tagging (via MusicBrainz) and folder organization.
*   **Deduplication**: Prevents duplicate downloads by scanning your existing library.
*   **Metadata Enrichment**: Automatically applies ID3 tags (Artist, Title, Album, Year, Cover Art) to fallback downloads.
*   **State Persistence**: Uses SQLite to track artist addition timestamps and fallback status.

---

## 🛠️ Architecture

1.  **ListenBrainz API**: Source of personalized recommendations.
2.  **Lidarr**: Primary acquisition engine (Torrents/Usenet).
3.  **yt-dlp**: Fallback acquisition engine (YouTube Audio).
4.  **MusicBrainz**: Metadata provider for tagging fallback tracks.
5.  **Navidrome/Jellyfin**: Final destination (structured library).

---

## 📋 Prerequisites

*   **Lidarr**: Installed and reachable via network/Docker.
*   **yt-dlp**: Installed on the host or inside the container.
*   **ListenBrainz Account**: To get personalized recommendations.
*   **Python 3.10+**: If running outside Docker.

---

## ⚙️ Configuration (.env)

Lyra uses a decoupled path system to ensure compatibility between your local filesystem and Lidarr's internal environment.

| Variable | Description | Example |
| :--- | :--- | :--- |
| `LIDARR_URL` | Your Lidarr instance URL | `http://192.168.1.10:8686` |
| `LIDARR_API_KEY` | Your Lidarr API Key | `your_api_key_here` |
| `LIDARR_ROOT_FOLDER_PATH` | The path **Lidarr** uses internally | `/music` |
| `LIBRARY_PATH` | The path **Lyra (this script)** uses locally | `/mnt/Storage2/data/media/Music` |
| `LISTENBRAINZ_USERNAME` | Your ListenBrainz username | `samvivan` |
| `LISTENBRAINZ_API_TOKEN` | Your ListenBrainz API token | `your_token_here` |
| `MAX_ARTISTS_PER_RUN` | Max artists to add in one run | `5` |
| `FALLBACK_HOURS` | Hours to wait before YouTube fallback | `24` |

---

## 🐳 Docker Setup (Recommended)

1.  **Clone & Configure**:
    ```bash
    git clone https://github.com/your-username/lyra.git
    cd lyra
    cp .env.example .env
    # Edit .env with your credentials
    ```

2.  **Launch**:
    ```bash
    docker-compose up -d --build
    ```

---

## 💻 Usage

### Automated Pipeline
Run the main automation logic (typically via Cron or the built-in loop mode):
```bash
python main.py [flags]
```
*   `--dry-run`: See what would happen without making changes.
*   `--limit X`: Override the max artists added.
*   `--add-only`: Only add new artists from ListenBrainz.
*   `--fallback-only`: Only check and run YouTube fallbacks.

### Manual Interactive Downloader
For specific tracks or albums not covered by automation:
```bash
# Host
python cli.py

# Docker
docker exec -it lyra python cli.py
```
*Tip: Create an alias for quick access:* `alias musicdl="docker exec -it lyra python cli.py"`

---

## 📁 Path Mapping Explanation

If you run Lidarr in Docker, it might map its internal `/music` folder to your host's `/mnt/Storage2/data/media/Music`. 

*   Set `LIDARR_ROOT_FOLDER_PATH=/music` (Lidarr needs this to know where to move files).
*   Set `LIBRARY_PATH=/mnt/Storage2/data/media/Music` (Lyra needs this to download YouTube tracks directly to your drive).

---

## 📝 Logs
Lyra maintains detailed logs for troubleshooting:
*   Standard output (Docker logs)
*   `music_automation.log` file

---

## 📜 License
MIT License. Feel free to use and modify for your homelab!
