from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from .models import SessionRecord

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

_UPSERT = """
INSERT OR REPLACE INTO sessions
    (session_id, source, model, date, start_ts, end_ts, project,
     turns, tool_calls, input_tokens, output_tokens,
     cache_creation_tokens, cache_read_tokens,
     context_peak_tokens, reasoning_tokens)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class UsageStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._db_path)

    def _migrate(self) -> None:
        with self._connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "usage" in tables and "sessions" not in tables:
                print(
                    "Warning: dropping old 'usage' table. "
                    "Re-run: python3 tracker.py collect --lookback 90",
                    file=sys.stderr,
                )
                conn.execute("DROP TABLE usage")
            conn.execute(_CREATE_SESSIONS)
            conn.commit()

    def upsert(self, records: list[SessionRecord]) -> int:
        if not records:
            return 0
        with self._connect() as conn:
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
