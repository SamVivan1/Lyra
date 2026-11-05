import os
import logging
import time

def setup_logger(name, level=logging.INFO):
    """Set up and return a configured logger."""
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(level)

        # Console Handler
        ch = logging.StreamHandler()
        ch.setLevel(level)

        # File Handler - use /app/data if available (Docker), else cwd
        log_dir = "/app/data" if os.path.isdir("/app/data") else "."
        log_path = os.path.join(log_dir, "music_automation.log")
        fh = logging.FileHandler(log_path)
        fh.setLevel(level)

        # Format
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        fh.setFormatter(formatter)

        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger

def rate_limit(seconds=2):
    """A simple decorator/function to sleep for rate limiting."""
    time.sleep(seconds)
