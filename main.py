import argparse
import sys
import time
from config import Config
from listenbrainz import ListenBrainzClient
from lidarr import LidarrClient
from fallback import FallbackOrchestrator
from state import StateManager
from utils import setup_logger

logger = setup_logger("main")

def add_artists(limit, dry_run):
    logger.info("Starting artist addition process...")
    try:
        Config.validate()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    lb_client = ListenBrainzClient()
    lidarr_client = LidarrClient()
    state_manager = StateManager()

    recommendations = lb_client.get_recommended_artists_with_tracks()
    if not recommendations:
        logger.info("No recommendations found.")
        return

    existing_artists = lidarr_client.get_existing_artists()
    existing_artists_lower = [a.lower() for a in existing_artists]
    tracked_artists = state_manager.get_tracked_artists()
    tracked_artists_lower = [a.lower() for a in tracked_artists]

    added_count = 0
    for artist, tracks in recommendations.items():
        if added_count >= limit:
            logger.info(f"Reached limit of {limit} artists per run.")
            break

        # Check blacklist (hardcoded for now, could be in config)
        if artist.lower() == "various artists":
            continue

        if artist.lower() in existing_artists_lower:
            logger.debug(f"Artist '{artist}' is already in Lidarr. Skipping.")
            continue
            
        if artist.lower() in tracked_artists_lower:
            logger.debug(f"Artist '{artist}' is already tracked by the state manager. Skipping.")
            continue

        logger.info(f"Adding new artist: {artist}")
        if dry_run:
            logger.info(f"[DRY RUN] Would add '{artist}' to Lidarr and state with {len(tracks)} tracks.")
            added_count += 1
        else:
            success = lidarr_client.add_artist(artist)
            if success:
                state_manager.add_artist(artist, recommended_tracks=tracks)
                added_count += 1

    logger.info(f"Finished adding {added_count} artists.")

def run_fallback(dry_run):
    orchestrator = FallbackOrchestrator()
    orchestrator.run_fallback(dry_run=dry_run)

def main():
    parser = argparse.ArgumentParser(description="Self-Hosted Music Automation System")
    parser.add_argument("--dry-run", action="store_true", help="Do not perform any actual operations")
    parser.add_argument("--limit", type=int, default=Config.MAX_ARTISTS_PER_RUN, help="Limit number of artists added per run")
    parser.add_argument("--add-only", action="store_true", help="Only add artists from ListenBrainz to Lidarr")
    parser.add_argument("--fallback-only", action="store_true", help="Only run the fallback (YouTube dl) logic")
    parser.add_argument("--loop", action="store_true", help="Run continuously in a loop")
    parser.add_argument("--interval", type=int, default=21600, help="Loop interval in seconds (default: 6 hours)")

    args = parser.parse_args()

    def job():
        if args.fallback_only:
            run_fallback(args.dry_run)
        elif args.add_only:
            add_artists(args.limit, args.dry_run)
        else:
            add_artists(args.limit, args.dry_run)
            run_fallback(args.dry_run)

    if args.loop:
        logger.info(f"Starting in loop mode, running every {args.interval} seconds.")
        while True:
            try:
                job()
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
            logger.info(f"Sleeping for {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        logger.info("Running in one-shot mode.")
        job()

if __name__ == "__main__":
    main()
