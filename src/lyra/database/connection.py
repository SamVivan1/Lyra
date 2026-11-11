"""
Database connection and initialization utilities.

Handles SQLite connections and schema initialization. The schema itself lives in
:mod:`lyra.database.migrations`; this module only opens connections, decides
whether a file is corrupt rather than merely busy, and applies the concurrency
pragmas.
"""

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional, Union

from ..config import Config
from .errors import DatabaseCorruptError, DatabaseFileError
from .migrations import apply_migrations

logger = logging.getLogger(__name__)

#: Per-connection SQLite pragmas. ``busy_timeout`` and ``synchronous`` are
#: connection settings, so they must be applied to every new connection.
BUSY_TIMEOUT_MS = 5000
#: Journal mode the concurrency guarantee of this module depends on. It is a
#: file property: setting it once is enough, but the answer must be checked.
JOURNAL_MODE = "wal"
#: Attempts at the journal-mode switch before it is reported as not achieved.
#: Only lock contention is retried; a negative answer is final.
WAL_ATTEMPTS = 3
#: Wait between those attempts. The competing lock is another process starting
#: up, which holds it for the milliseconds its migration takes.
WAL_RETRY_DELAY_S = 0.05


def integrity_ok(db_path: Union[str, Path]) -> bool:
    """
    Return True only when ``PRAGMA integrity_check`` reports the file as ``ok``.

    This is the one authority on whether a database is corrupt; a failed query
    is not evidence. A file that is not a database at all makes the pragma
    *raise* instead of returning a row, so that case counts as corrupt as well.
    A lock is re-raised untouched: waiting for the other writer is the caller's
    business and is never a reason to call the file damaged.

    Args:
        db_path: Path of the database file to check.

    Returns:
        True if SQLite reports the file as ``ok``, False otherwise.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        raise
    except sqlite3.DatabaseError:
        return False
    return bool(row) and str(row[0]).lower() == "ok"


class DatabaseConnection:
    """
    SQLite connection factory with an explicit two-phase lifecycle.

    The constructor only records the database path; it never touches disk, so
    constructing many instances in tests is free and hermetic. Call
    ``initialize()`` once before use to create the parent directory, build the
    schema and apply the concurrency PRAGMAs.
    """

    def __init__(self, path: Optional[Union[str, Path]] = None) -> None:
        """
        Record the database path without touching disk.

        Args:
            path: Database file path. Defaults to ``Config.DB_PATH``.
        """
        self.path = Path(path or Config.DB_PATH)
        self._initialized = False

    def initialize(self) -> None:
        """
        Create the database file, migrate the schema and apply concurrency PRAGMAs.

        Opening the file comes first, on purpose: every PRAGMA and every migration
        statement raises ``sqlite3.DatabaseError`` on a file that is not a
        database, and ``_migrate()`` is where that failure gets classified.
        Nothing here repairs a file: a corrupt database raises
        :class:`DatabaseCorruptError` and stays untouched until an operator runs
        ``lyra --repair-db``.

        Must be called before ``get_connection()`` is used.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DatabaseFileError(self.path, "create the directory for", e) from e
        self._migrate()
        self._initialized = True
        logger.info(f"Database initialized at {self.path}")

    def _migrate(self) -> None:
        """
        Apply schema migrations, translating an unusable file into an actionable error.

        A lock is re-raised untouched: it is a concurrency condition, and the old
        recovery path renamed the live file for it, destroying a database that was
        merely busy. Any other ``sqlite3.DatabaseError`` is only called
        corruption when ``PRAGMA integrity_check`` agrees; a query that fails
        while the file checks out is re-raised as it came.

        The migration and the PRAGMAs share the connection because the pragmas
        below raise on a file that is not a database at all, and that failure
        belongs to the same classification. The journal-mode switch runs on its
        own connection, deliberately: another process may have switched the file
        to WAL while this one was migrating, and a connection that still holds
        the old journal-mode view fails that switch with ``disk I/O error``.
        """
        try:
            with self.get_connection() as conn:
                apply_migrations(conn, self.path)
            with self.get_connection() as conn:
                self._enable_wal(conn)
                self._apply_pragmas(conn)
        except sqlite3.OperationalError:
            raise
        except sqlite3.DatabaseError as e:
            if integrity_ok(self.path):
                raise
            raise DatabaseCorruptError(self.path, str(e)) from e
        logger.debug("Database schema migrated/verified")

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """Apply the per-connection concurrency pragmas."""
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous=NORMAL")

    def _enable_wal(self, conn: sqlite3.Connection) -> str:
        """
        Put the database file in WAL mode and return the mode SQLite reports.

        The returned row is checked rather than discarded: if the file cannot be
        switched to WAL, readers block on open writer transactions, which is the
        exact behaviour this module exists to avoid. A filesystem without WAL
        support is not fatal (the database still works), so this warns loudly
        instead of raising and keeps startup alive; the warning is the only
        signal that the concurrency guarantee is not being met.

        A lock is not fatal either, and it is the one failure that is worth
        retrying: switching the journal mode takes the file's exclusive lock, and
        SQLite answers ``SQLITE_BUSY`` without waiting when a second connection
        holds a lock and asks for one at the same time -- which is exactly what a
        process starting beside this one does. The other process sets the same
        file property, so losing the race costs nothing but a retry.
        """
        mode = ""
        last_error: Optional[sqlite3.OperationalError] = None
        for attempt in range(1, WAL_ATTEMPTS + 1):
            try:
                row = conn.execute(
                    f"PRAGMA journal_mode={JOURNAL_MODE.upper()}"
                ).fetchone()
            except sqlite3.OperationalError as e:
                last_error = e
                if attempt < WAL_ATTEMPTS:
                    time.sleep(WAL_RETRY_DELAY_S)
                continue
            last_error = None
            # A definite answer, even a negative one, is final: only contention
            # is worth another attempt.
            mode = (row[0] if row else "") or ""
            break
        if last_error is not None:
            logger.warning(
                f"Could not switch {self.path} to WAL: {last_error}. Another "
                "process is using the database; concurrent readers may block on "
                "open writer transactions until the journal mode is WAL."
            )
        elif mode.lower() != JOURNAL_MODE:
            logger.warning(
                f"SQLite did not enable WAL for {self.path}: "
                f"journal_mode returned {mode!r} instead of {JOURNAL_MODE!r}. "
                "Concurrent readers may block on open writer transactions."
            )
        return mode

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Get a thread-safe database connection context manager.

        The connection is closed even when applying the pragmas raises, so a
        failing connection can never be left open.
        
        Yields:
            sqlite3.Connection: Database connection with Row factory enabled.
        """
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        try:
            # Inside the try block on purpose: a pragma that raises (a corrupt
            # file gives ``file is not a database``) must not leave the
            # connection open. It is not logged here; callers that classify,
            # such as ``_migrate()``, do their own reporting.
            conn.row_factory = sqlite3.Row
            self._apply_pragmas(conn)
            try:
                yield conn
            except sqlite3.Error as e:
                logger.error(f"Database error: {e}")
                conn.rollback()
                raise
        finally:
            conn.close()

