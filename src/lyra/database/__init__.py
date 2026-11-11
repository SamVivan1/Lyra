"""
Database module: Manages local library cache and track state persistence.

This module provides abstractions for:
- StateManager: Core database abstraction for artists, tracks, and library state
- LibraryScanner: Scans filesystem and indexes audio files
- TrackStateManager: Manages individual track fallback state
"""

from .library_scanner import LibraryScanner
from .state import StateManager
from .track_manager import TrackStateManager

__all__ = ["StateManager", "LibraryScanner", "TrackStateManager"]
