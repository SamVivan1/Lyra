# Role & Objective
You are an expert Python Auditor and QA Engineer. Your task is to audit the current workflow of this Python application, compare it with the desired workflow below, and generate a comprehensive `pytest` suite to verify if the application behaves exactly as expected.

## Tech Stack Context
- **Language:** Python 3.x
- **Testing Framework:** `pytest` (with `pytest-mock` or `unittest.mock`)
- **Primary Music Server:** Navidrome (Filesystem-based music library)
- **Automation Tools:** Lidarr API (for adding tracks), `yt-dlp` (for fallback downloads)
- **Database:** Local Database (SQLite/PostgreSQL) used as a local library cache.

---

## The Desired Workflow Specification

### 1. Library Initialization & Local Caching (The Source of Truth)
- **First-Run Behavior:** The application must perform a full filesystem scan of the music library directory (the same folder Navidrome reads). It must extract metadata (Artist, Album, Title, Track MBID if available) and store it in a local database table named `local_library`.
- **Subsequent Runs:** The application **must not** rescan the entire disk. It should query the local database to check if a song exists. 
- **Manual Trigger:** Provide a CLI command or flag (e.g., `--rescan`) to force a database refresh when requested by the user.

### 2. Automated ListenBrainz Sync & Fallback Logic (24-Hour Rule)
- **Step 1:** Fetch recommendation JSON from:
  `GET https://api.listenbrainz.org/1/cf/recommendation/user/{username}/recording`
- **Step 2:** Parse `recording_mbid` and resolve the Artist and Track title via MusicBrainz API.
- **Step 3 (The Check):** Search for the track inside the **local application database** (`local_library`). 
  - If FOUND: Skip (do nothing).
  - If NOT FOUND: Add the track to Lidarr via Lidarr API so Lidarr can attempt to index and download it through standard usenet/torrents. Save this tracking state in a table `tracked_tracks` with a timestamp (`added_at`) and status `PENDING`.
- **Step 4 (The 24-Hour Fallback Evaluation):** - A background worker evaluates entries in `tracked_tracks` where status is `PENDING` and `time_elapsed > 24 hours`.
  - Instead of checking Lidarr API (which can be inaccurate), the app must perform a quick targeted filesystem check or targeted database re-verification for that specific track.
  - If the file is still missing after 24 hours, trigger **YouTube Fallback Mode**:
    1. Search and download the audio using `yt-dlp` (`"{Artist} - {Title}"`).
    2. Inject proper ID3 metadata tags into the downloaded file.
    3. Move the file into the music library directory.
    4. Update the local database (`local_library` and `tracked_tracks` to `COMPLETED`).

### 3. Manual Interactive CLI Downloader
- **Mode A (Search String):** Prompt the user for a query ➔ Search YouTube ➔ Display Top 5 interactive options ➔ Download selection ➔ Tag metadata ➔ Move to library ➔ Update local database.
- **Mode B (Direct Link):** Accept YouTube/Spotify URLs ➔ Resolve metadata ➔ Download ➔ Format and tag accurately for ListenBrainz scrobbling compatibility ➔ Update local database.

---

# Your Tasks:
1. **Audit & Compare:** Analyze the existing Python codebase. Identify structural differences between how it currently works vs. the Desired Workflow specified above. Point out what needs to be refactored.
2. **Architecture Advice:** Confirm if the current implementation can achieve this goal smoothly or if certain modules need a complete rewrite.
3. **Generate Pytest Suite:** Create a `tests/` directory containing `pytest` files that mock external APIs (ListenBrainz, MusicBrainz, Lidarr) and the filesystem. Ensure you write tests covering:
   - Initial library scanning and local database population.
   - Cache hit (skipping download if track exists in the local DB).
   - 24-hour expiration trigger checking the local database/targeted path instead of Lidarr's API.
   - YouTube download fallback and subsequent local DB update.
