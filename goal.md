Build a production-ready, self-hosted Python service that automates music acquisition using a hybrid pipeline:

Primary: ListenBrainz → Lidarr (torrent-based download)
Fallback: YouTube download for missing content after a delay

This system is intended for a homelab environment and must be robust, modular, and fully automated.

---

## 🎯 Goals

1. Fetch music recommendations from ListenBrainz
2. Add recommended artists into Lidarr
3. Monitor download status via Lidarr
4. If content is not downloaded after 24 hours, trigger fallback
5. Fallback downloads specific tracks from YouTube
6. Store all music in a structured library for Navidrome

---

## 📁 Fixed Paths (MUST USE)

* Music library:
  /mnt/Storage2/data/media/Music

* Download directory (used by torrent client):
  /DATA/Downloads

---

## 🧠 Architecture

ListenBrainz API
→ Recommendation Service
→ Lidarr API (artist-based acquisition)
→ Torrent Client (handled by Lidarr)
→ Monitor Missing Content
→ Fallback Engine (YouTube via yt-dlp)
→ Music Library
→ Navidrome

---

## 🔧 Core Features

### 1. ListenBrainz Integration

* Endpoint:
  https://api.listenbrainz.org/1/recommendation/user/{username}
* Extract artist names from:
  payload.recordings[].artist_credit[].artist_name
* Deduplicate artists

---

### 2. Lidarr Integration

* GET /api/v1/artist → retrieve existing artists
* POST /api/v1/artist → add new artist

Payload must include:

* artistName
* qualityProfileId
* metadataProfileId
* rootFolderPath = /mnt/Storage1/data/media/Music
* monitored = true
* addOptions.searchForMissingAlbums (configurable)

---

### 3. Artist Filtering

* Skip artists already in Lidarr (case-insensitive)
* Support blacklist (e.g. "Various Artists")
* Limit number of artists added per run (default: 5)

---

### 4. State Tracking (IMPORTANT)

Implement persistent state tracking using SQLite (preferred) or JSON fallback.

Track per artist:

* added_at (timestamp)
* fallback_done (boolean)

---

### 5. Missing Content Detection

* Use Lidarr endpoint:
  GET /api/v1/wanted/missing
* Identify artists/albums still missing
* Match with tracked artists

---

### 6. Fallback Logic (KEY FEATURE)

Trigger fallback if:

* Artist exists in state
* fallback_done = false
* Current time - added_at > 24 hours

---

### 7. Track Selection for Fallback

From ListenBrainz:

* Use user listening history endpoint:
  https://api.listenbrainz.org/1/user/{username}/listens
* Extract tracks matching the target artist
* Deduplicate track names
* Select top 1–2 tracks per artist

---

### 8. YouTube Download (Fallback)

Use yt-dlp:

* Search query:
  "{artist} {track} official audio"
* Extract audio:
  --extract-audio
  --audio-format mp3
  --audio-quality 0

Output path:
/mnt/Storage1/data/media/Music/%(artist)s - %(title)s.%(ext)s

---

### 9. Optional Post-processing

* Add optional integration with beets for tagging and metadata cleanup
* Must be modular (can be enabled/disabled)

---

### 10. Deduplication

* Avoid downloading duplicate tracks
* Check if file already exists in music directory

---

### 11. Logging

* Log all operations:

  * artist added
  * artist skipped
  * fallback triggered
  * download success/failure
* Output to console + log file with timestamps

---

### 12. Error Handling

* Retry failed API calls (max 3 retries)
* Gracefully skip malformed entries
* Do not crash entire service on failure

---

### 13. Rate Limiting

* Add delay between API calls (e.g. 2 seconds)

---

### 14. CLI Support

Support flags:

* --dry-run (no actual Lidarr or download actions)
* --limit (override number of artists per run)
* --fallback-only
* --add-only

---

### 15. Scheduling

Support two modes:

1. One-shot execution (for cron)
2. Loop mode (runs continuously)

Provide example cron:

* Every 6 hours → add artists
* Every 6 hours offset → fallback check

---

### 16. Project Structure

music-automation/

* main.py
* config.py
* listenbrainz.py
* lidarr.py
* fallback.py
* state.py
* downloader.py
* utils.py

---

### 17. Configuration via Environment Variables

* LISTENBRAINZ_USERNAME
* LIDARR_URL
* LIDARR_API_KEY
* QUALITY_PROFILE_ID
* METADATA_PROFILE_ID
* MAX_ARTISTS_PER_RUN
* FALLBACK_HOURS (default: 24)

---

### 18. Docker Support

Provide:

* Dockerfile (lightweight Python image)
* docker-compose.yml
* .env.example

Volume mounts:

* /mnt/Storage1/data/media/Music
* /DATA/Downloads

---

### 19. Testing

* Provide mock ListenBrainz response
* Provide test mode or sample run
* Include example logs

---

## ⚡ Constraints

* Use minimal dependencies (requests, sqlite3, subprocess)
* Avoid heavy frameworks (no Django/FastAPI)
* Code must be clean, modular, readable
* Must run well in low-resource homelab

---

## 🎁 Bonus Features (if possible)

* Track success rate (torrent vs fallback)
* Cache failed artists
* Simple stats output (JSON or CLI)

---

## 📦 Expected Output

* Fully working Python project
* Dockerized setup
* Clear README.md with setup instructions
* Example .env file
* Ready to deploy in homelab

---

The final system should behave like a self-healing music acquisition pipeline:

* prioritize high-quality torrent downloads via Lidarr
* automatically fallback to YouTube when content is unavailable
* continuously grow a personal music library for Navidrome
