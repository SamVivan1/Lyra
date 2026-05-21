"""
CLI module: Command-line interface and interactive components.

Provides interactive user interfaces for manual track downloads.
"""

from .interactive import InteractiveDownloader
from .db_integration import CLIDatabaseIntegration

__all__ = ["InteractiveDownloader", "CLIDatabaseIntegration"]
