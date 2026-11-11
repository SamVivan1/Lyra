"""
Errors raised by the database layer.

The distinction between these classes is the point of the module. A locked
database is a concurrency condition and keeps its ``sqlite3.OperationalError``;
a corrupt database needs an operator decision, so it gets its own error that
says how to repair it; a failed filesystem operation is neither and must never
leak as a bare ``OSError`` with no advice attached.
"""

from pathlib import Path
from typing import Union


class DatabaseCorruptError(RuntimeError):
    """
    The database file failed SQLite's ``PRAGMA integrity_check``.

    Raised instead of repairing the file: Lyra used to rename a database that
    merely failed a query, which could destroy a live file. The message names
    the file and the one command that repairs it on purpose.
    """

    def __init__(self, db_path: Union[str, Path], detail: str = "") -> None:
        """
        Build the error for a corrupt database file.

        Args:
            db_path: Path of the database file that failed the check.
            detail: Optional SQLite detail, such as ``file is not a database``.
        """
        self.db_path = Path(db_path)
        self.detail = detail
        detail_text = f" ({detail})" if detail else ""
        super().__init__(
            f"Database file {self.db_path} is corrupt or unreadable{detail_text}. "
            "Lyra does not rename or rebuild it automatically, because that "
            "destroys data and hides the problem. Back it up and rebuild it with: "
            "lyra --repair-db"
        )


class DatabaseFileError(RuntimeError):
    """
    A filesystem operation on the database file failed.

    Used where a bare ``OSError`` would reach the operator with no context: a
    read-only directory, a full disk, or a rename that could not happen. The
    message names the path, the operation and the original reason.
    """

    def __init__(
        self,
        db_path: Union[str, Path],
        operation: str,
        reason: Union[str, OSError],
    ) -> None:
        """
        Build the error for a failed filesystem operation.

        Args:
            db_path: Path of the database file the operation was for.
            operation: What was attempted, e.g. ``back it up``.
            reason: The underlying ``OSError`` or a readable explanation.
        """
        self.db_path = Path(db_path)
        self.operation = operation
        self.reason = reason
        super().__init__(
            f"Could not {operation} the database at {self.db_path}: {reason}. "
            "Check that the directory exists and is writable."
        )
