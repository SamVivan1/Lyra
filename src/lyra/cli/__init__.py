"""
CLI module: Command-line interface and interactive components.

Provides interactive user interfaces for manual track downloads.
"""

from .db_integration import CLIDatabaseIntegration
from .interactive import InteractiveDownloader

__all__ = ["InteractiveDownloader", "CLIDatabaseIntegration"]
