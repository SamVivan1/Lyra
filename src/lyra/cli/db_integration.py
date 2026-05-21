"""
CLI database integration utilities.

Handles database updates from CLI operations.
"""

import logging
from typing import Optional

from ..database.state import StateManager
from ..database.connection import DatabaseConnection

logger = logging.getLogger(__name__)


class CLIDatabaseIntegration:
    """
    Integrates CLI operations with database.
    
    Updates local library when user downloads tracks manually.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        db_connection: Optional[DatabaseConnection] = None
    ):
        """
        Initialize CLIDatabaseIntegration.
        
        Args:
            state_manager: StateManager instance
            db_connection: DatabaseConnection instance
        """
        self.state_manager = state_manager or StateManager(db_connection)

    def record_manual_download(
        self,
        artist: str,
        title: str,
        album: Optional[str] = None,
        file_path: Optional[str] = None
    ) -> bool:
        """
        Record a manually downloaded track in the database.
        
        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional)
            file_path: Path to downloaded file (optional)
            
        Returns:
            True if successfully recorded
        """
        logger.info(f"Recording manual download: '{artist} - {title}'")
        
        success = self.state_manager.add_local_track(
            artist,
            title,
            album=album,
            file_path=file_path,
            added_at=self.state_manager._now_iso()
        )
        
        if success:
            logger.info(f"Recorded download in local library: '{artist} - {title}'")
        else:
            logger.warning(f"Failed to record download: '{artist} - {title}'")
        
        return success
