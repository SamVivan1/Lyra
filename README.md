# Lyra: Self-Hosted Music Automation System 🎵

Lyra is a modular Python package for homelab music automation. It integrates ListenBrainz recommendations with Lidarr and falls back to YouTube downloads using `yt-dlp` when content is unavailable.

## 🚀 What Lyra Does

- Fetches artist recommendations from **ListenBrainz**.
- Adds new artists to **Lidarr** for automated downloads.
- Tracks recommended songs for fallback processing.
- After a configurable delay, automatically downloads missing songs from **YouTube**.
- Applies metadata tags using **MusicBrainz** and stores local state in **SQLite**.

## 📦 Package Layout

The application code lives inside `src/lyra/` and follows a clean architecture with separated layers:

- `src/lyra/api/` — external API clients
- `src/lyra/database/` — SQLite state and library scanning
- `src/lyra/downloader/` — YouTube download and metadata tagging
- `src/lyra/orchestrator/` — workflow orchestration
- `src/lyra/cli/` — interactive CLI utilities

## ⚙️ Installation

```bash
git clone https://github.com/your-username/lyra.git
cd lyra
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For development:

```bash
pip install -e .
```

## 🧩 Configuration

Copy the example environment file and update values:

```bash
cp .env.example .env
```

Required variables:

- `LISTENBRAINZ_USERNAME`
- `LISTENBRAINZ_API_TOKEN`
- `LIDARR_API_KEY`

Optional configuration is documented in `docs/CONFIGURATION.md`.

## ▶️ Run Lyra

### One-shot mode

```bash
python -m lyra
```

### Loop mode

```bash
python -m lyra --loop
```

### Interactive downloader

```bash
python -m lyra --interactive
```

### Common flags

- `--dry-run` — simulate actions without making changes
- `--limit X` — maximum artists to add in one run
- `--add-only` — only add artists to Lidarr
- `--fallback-only` — only run fallback downloads
- `--log-level DEBUG` — enable detailed logging

## 🐳 Docker

Build and start the service:

```bash
docker-compose up -d --build
```

The container uses `PYTHONPATH=/app/src` so the package runs from the source tree.

## 📚 Documentation

- `docs/ARCHITECTURE.md`
- `docs/DATABASE.md`
- `docs/CONFIGURATION.md`
- `docs/DEPLOYMENT.md`
- `docs/DEVELOPMENT.md`

## 🧪 Running Tests

```bash
pytest
```

## 🔧 Notes

- A local SQLite database persists state in `music_automation.db` by default.
- Library paths should align with your Lidarr container mount settings.
- Logs are written to stdout and `music_automation.log`.
