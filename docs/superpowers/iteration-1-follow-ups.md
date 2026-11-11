# Iteration 1 — follow-ups for iterations 2 and 3

Recorded when iteration 1 (data safety) merged to `main`. These are the items deliberately deferred during implementation and review, so the next iteration starts from a list instead of re-discovering them.

Iteration 1 delivered: two-phase `DatabaseConnection` with WAL/`busy_timeout`, lock-is-not-corruption with explicit `--repair-db`, versioned migrations v1→v2 with the natural key and duplicate reports, state-machine columns on `tracked_tracks`, real MBID extraction, `dry_run` as a store property, `.dockerignore` plus a hermetic guard, and CI.

## Must fix before or early in iteration 2

1. **`file_path` can be nulled by an upsert.** `state.py`'s `ON CONFLICT DO UPDATE` sets `file_path = excluded.file_path`; `cli/db_integration.py` records a manual download without a path, so re-recording an already-indexed artist+title deterministically blanks a known path. A later scan restores it, but nothing should blank a path. Fix: `COALESCE(excluded.file_path, local_library.file_path)`.
2. **`Config.DB_PATH` is still relative.** It resolves against the working directory, so running from two directories produces two databases — the symptom that produced the two `music_automation.db` files in the repo. Design §8.6. Also log the resolved absolute path at startup.
3. **`--dry-run` alone still writes the database** when the schema is behind: `initialize()` migrates (and takes a backup) before `args.dry_run` is read. Decide and document whether dry-run suppresses migration.
4. **Audit fixes that `main` carried and the rewrite never had**: glob escaping in the library check (`glob.escape`), exact-match verification in `add_artist`, pagination on `/api/v1/wanted/missing`, and the Docker log path. They were lost with the old flat modules; iteration 2 owns them.
5. **`--repair-db` has no guard against a healthy database.** It backs up and rebuilds empty. Add `--force` or refuse when `integrity_check` passes.

## Iteration 2 (flow & state machine)

- Two `dry_run` disagreement policies survive: a per-call `dry_run` on a wet orchestrator still writes rows, and injecting a dry store into a wet orchestrator is unguarded. The per-call parameter should be removed so dry-run has one owner.
- `get_pending_tracked_tracks` still filters `status = 'NEW'` with an age cutoff in Python; `ix_tracked_queue(status, next_retry_at)` has no reader yet. The queue described in `docs/DATABASE.md` is the target, not the current code.
- `FALLBACK_HOURS` was repurposed in the design (maximum wait inside `WAITING_LIDARR`) but iteration 1 code still uses it as the age cutoff. Reconcile when the Lidarr queue check lands.
- `m4a` files contribute no MBID: Picard stores it in the MP4 freeform atom `----:com.apple.iTunes:MusicBrainz Track Id`, which is not among the keys read.
- `mutagen.File` is opened twice per scanned file (easy for artist/title/album, non-easy for the MBID). One non-easy open would halve tag parsing on a large library.
- `_track_exists` still uses a filesystem glob rather than the database; title sanitisation and `glob.escape` are still owed.
- The corrupt-detection is reactive only; there is no post-migration `integrity_check` even though design §10 asks for one.
- A second starter can still fail to start if the winning migration exceeds `busy_timeout`; the two-process test uses a 4-row database and cannot catch it. Cheap mitigation: keep the longer migration timeout, and consider the same for the WAL-switch connection.
- `maintenance.py` removes the old `-wal`/`-shm` before `os.replace`, so a failed rename leaves the main file without its WAL (the commits survive in the backup, which the error names).

## Iteration 3 (TUI)

- `python -m lyra --interactive` under a pipe still tracebacks with `termios.error`; the menu loops forever on stdin EOF; `_clear_screen()` after `return None` in `_select_with_hjkl` is unreachable.
- `interactive.py` still hand-rolls raw mode instead of using `questionary` (declared but unimported), and rich markup is parsed from untrusted video titles.
- The root `cli.py` and `main.py` shims duplicate the package entry points and keep the uninitialised-database bug; the quality review recommends deleting both.
- `src/lyra.egg-info` is still tracked and now stale against `pyproject.toml`; untrack it and add `*.egg-info/` to `.gitignore`.
- `docker-compose.yml` still has no healthcheck, and `Dockerfile` runs as root.
- Coverage gate is scoped to `lyra.database` only; `downloader.py`, `orchestrator`, `cli` and `main.py` are outside it, which is where the dry-run enforcement lives.
- `README.md` and `docs/DEPLOYMENT.md` still claim the image sets `PYTHONPATH`; `docs/DATABASE.md`'s `artists` block and singleton section are stale.
