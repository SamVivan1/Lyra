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
        
        # File Handler
        fh = logging.FileHandler("music_automation.log")
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
