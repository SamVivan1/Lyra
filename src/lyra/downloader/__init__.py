"""
Downloader module: Audio file downloading and metadata management.

Provides abstractions for:
- Downloader: Core YouTube downloading via yt-dlp
- MetadataHandler: ID3 tag management
- FallbackOrchestrator: YouTube fallback orchestration
"""

from .downloader import Downloader
from .fallback_orchestrator import FallbackOrchestrator
from .metadata_handler import MetadataHandler

__all__ = ["Downloader", "MetadataHandler", "FallbackOrchestrator"]
