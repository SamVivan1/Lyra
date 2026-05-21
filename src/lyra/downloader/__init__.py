"""
Downloader module: Audio file downloading and metadata management.

Provides abstractions for:
- Downloader: Core YouTube downloading via yt-dlp
- MetadataHandler: ID3 tag management
- FallbackOrchestrator: YouTube fallback orchestration
"""

from .downloader import Downloader
from .metadata_handler import MetadataHandler
from .fallback_orchestrator import FallbackOrchestrator

__all__ = ["Downloader", "MetadataHandler", "FallbackOrchestrator"]
