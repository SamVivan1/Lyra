"""
CLI database integration utilities.

Handles database updates from CLI operations.
"""

import logging
from typing import Optional

from ..database.connection import DatabaseConnection
from ..database.state import StateManager

logger = logging.getLogger(__name__)


class CLIDatabaseIntegration:
    """
    Integrates CLI operations with database.
    
    Updates local library when user downloads tracks manually.
    """

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        db_connection: Optional[DatabaseConnection] = None,
        dry_run: bool = False
    ):
        """
        Initialize CLIDatabaseIntegration.
        
        Args:
            state_manager: StateManager instance
            db_connection: DatabaseConnection instance
            dry_run: If True, the state manager it builds reports success and
                writes nothing, so a simulated download cannot enter the library.
                A state_manager injected together with dry_run=True must itself
                be dry; the contradiction is refused below.
        """
        if (
            state_manager is not None
            and dry_run
            and getattr(state_manager, "dry_run", False) is not True
        ):
            raise ValueError(
                "CLIDatabaseIntegration(dry_run=True) was given a state_manager "
                "with dry_run=False: it would record a simulated download in a "
                "wet store. Build the StateManager with dry_run=True, or leave "
                "state_manager out so the integration builds a dry one."
            )
        self.state_manager = state_manager or StateManager(db_connection, dry_run=dry_run)

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
