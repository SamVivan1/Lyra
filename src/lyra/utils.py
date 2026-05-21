"""
Utility functions and helpers for Lyra.

Common utilities for string manipulation, validation, etc.
"""

import re
from typing import Optional


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename for filesystem safety.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename safe for filesystem
    """
    return re.sub(r'[<>:"/\\|?*\n\r]+', '_', (filename or '').strip())


def normalize_artist_name(artist: str) -> str:
    """
    Normalize artist name for comparison.
    
    Args:
        artist: Original artist name
        
    Returns:
        Normalized name (lowercase, stripped)
    """
    return (artist or '').lower().strip()


def extract_first_value(value) -> Optional[str]:
    """
    Extract first value from list or return string as-is.
    
    Args:
        value: Value (list, string, or other)
        
    Returns:
        First string value or None
    """
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None
