"""SQLite-backed session store with sync log."""
from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from ..models import SessionRecord
from . import SessionStore

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id             TEXT NOT NULL,
    source                 TEXT NOT NULL,
    model                  TEXT NOT NULL,
    date                   TEXT NOT NULL,
    start_ts               TEXT,
    end_ts                 TEXT,
    project                TEXT,
    turns                  INTEGER DEFAULT 0,
    tool_calls             INTEGER DEFAULT 0,
    input_tokens           INTEGER DEFAULT 0,
    output_tokens          INTEGER DEFAULT 0,
    cache_creation_tokens  INTEGER DEFAULT 0,
    cache_read_tokens      INTEGER DEFAULT 0,
    context_peak_tokens    INTEGER DEFAULT 0,
    reasoning_tokens       INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, source, model)
)
"""

_CREATE_SYNC_LOG = """
CREATE TABLE IF NOT EXISTS sync_log (
    session_id  TEXT NOT NULL,
    source      TEXT NOT NULL,
    model       TEXT NOT NULL,
    store_name  TEXT NOT NULL,
    synced_at   TEXT NOT NULL,
    PRIMARY KEY (session_id, source, model, store_name)
)
"""

_UPSERT = """
INSERT OR REPLACE INTO sessions
    (session_id, source, model, date, start_ts, end_ts, project,
     turns, tool_calls, input_tokens, output_tokens,
     cache_creation_tokens, cache_read_tokens,
     context_peak_tokens, reasoning_tokens)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class SqliteStore:
    """SQLite-backed session store implementing SessionStore protocol."""

    name = "sqlite"

    def __init__(self, db_path: Path | str) -> None:
        """Initialize the SQLite store and create schema if needed.

        Args:
            db_path: Path to the SQLite database file (Path or str).
        """
        self._db_path = Path(db_path) if isinstance(db_path, str) else db_path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        """Create a connection to the database, creating parent directories as needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._db_path)

    def _migrate(self) -> None:
        """Run database migrations: create tables and drop old schema if needed."""
        with closing(self._connect()) as conn, conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            # Drop old schema if it exists
            if "usage" in tables and "sessions" not in tables:
                print(
                    "Warning: dropping old 'usage' table. "
                    "Re-run: python3 tracker.py collect --lookback 90",
                    file=sys.stderr,
                )
                conn.execute("DROP TABLE usage")
            # Create new schema
            conn.execute(_CREATE_SESSIONS)
            conn.execute(_CREATE_SYNC_LOG)
            conn.commit()

    def upsert(self, records: list[SessionRecord]) -> int:
        """Persist records using INSERT OR REPLACE (last-write-wins).

        Args:
            records: List of SessionRecord objects to upsert.

        Returns:
            Count of records upserted.
        """
        if not records:
            return 0
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                _UPSERT,
                [
                    (
                        r.session_id, r.source, r.model, r.date,
                        r.start_ts, r.end_ts, r.project,
                        r.turns, r.tool_calls, r.input_tokens, r.output_tokens,
                        r.cache_creation_tokens, r.cache_read_tokens,
                        r.context_peak_tokens, r.reasoning_tokens,
                    )
                    for r in records
                ],
            )
        return len(records)

    def close(self) -> None:
        """Flush buffers and release resources."""
        # SQLite connections are managed via context manager,
        # so there's nothing to do here, but we implement the protocol.
        pass

    def unsynced_for(self, store_name: str) -> list[SessionRecord]:
        """Return all records not yet synced to the given store."""
        _UNSYNCED = """
        SELECT s.session_id, s.source, s.model, s.date, s.start_ts, s.end_ts,
               s.project, s.turns, s.tool_calls, s.input_tokens, s.output_tokens,
               s.cache_creation_tokens, s.cache_read_tokens,
               s.context_peak_tokens, s.reasoning_tokens
        FROM sessions s
        LEFT JOIN sync_log l
            ON s.session_id = l.session_id
            AND s.source = l.source
            AND s.model = l.model
            AND l.store_name = ?
        WHERE l.session_id IS NULL
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(_UNSYNCED, (store_name,)).fetchall()
        return [
            SessionRecord(
                session_id=row[0], source=row[1], model=row[2], date=row[3],
                start_ts=row[4], end_ts=row[5], project=row[6],
                turns=row[7], tool_calls=row[8], input_tokens=row[9],
                output_tokens=row[10], cache_creation_tokens=row[11],
                cache_read_tokens=row[12], context_peak_tokens=row[13],
                reasoning_tokens=row[14],
            )
            for row in rows
        ]

    def mark_synced(self, records: list[SessionRecord], store_name: str) -> None:
        """Record that the given records were successfully pushed to store_name."""
        if not records:
            return
        _MARK_SYNCED = """
        INSERT OR IGNORE INTO sync_log (session_id, source, model, store_name, synced_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                _MARK_SYNCED,
                [(r.session_id, r.source, r.model, store_name) for r in records],
            )
