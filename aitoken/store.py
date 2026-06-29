"""SQLite sink storing activity at the daily grain with idempotent upserts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import ActivityRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_activity (
  date                TEXT NOT NULL,
  source              TEXT NOT NULL,
  model               TEXT NOT NULL,
  scope               TEXT NOT NULL,
  sessions            INTEGER NOT NULL DEFAULT 0,
  prompts             INTEGER NOT NULL DEFAULT 0,
  turns               INTEGER NOT NULL DEFAULT 0,
  tool_calls          INTEGER NOT NULL DEFAULT 0,
  context_peak_tokens INTEGER NOT NULL DEFAULT 0,
  input_tokens        INTEGER NOT NULL DEFAULT 0,
  output_tokens       INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
  first_ts            TEXT,
  last_ts             TEXT,
  updated_at          TEXT NOT NULL,
  PRIMARY KEY (date, source, model, scope)
);
"""

_NEW_COLUMNS = [
    ("input_tokens",       "INTEGER NOT NULL DEFAULT 0"),
    ("output_tokens",      "INTEGER NOT NULL DEFAULT 0"),
    ("cache_read_tokens",  "INTEGER NOT NULL DEFAULT 0"),
    ("cache_write_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("reasoning_tokens",   "INTEGER NOT NULL DEFAULT 0"),
]

_UPSERT = """
INSERT INTO daily_activity
  (date, source, model, scope, sessions, prompts, turns, tool_calls,
   context_peak_tokens, input_tokens, output_tokens, cache_read_tokens,
   cache_write_tokens, reasoning_tokens, first_ts, last_ts, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(date, source, model, scope) DO UPDATE SET
  sessions            = excluded.sessions,
  prompts             = excluded.prompts,
  turns               = excluded.turns,
  tool_calls          = excluded.tool_calls,
  context_peak_tokens = excluded.context_peak_tokens,
  input_tokens        = excluded.input_tokens,
  output_tokens       = excluded.output_tokens,
  cache_read_tokens   = excluded.cache_read_tokens,
  cache_write_tokens  = excluded.cache_write_tokens,
  reasoning_tokens    = excluded.reasoning_tokens,
  first_ts            = excluded.first_ts,
  last_ts             = excluded.last_ts,
  updated_at          = excluded.updated_at;
"""


class UsageStore:
    """Owns the tracker's SQLite database.

    Re-collecting a day overwrites that day's rows (each collector already
    aggregates a full day from cumulative source files), which keeps repeated
    runs idempotent.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        self._migrate(conn)
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_activity)")}
        for col, defn in _NEW_COLUMNS:
            if col not in existing:
                conn.execute(f"ALTER TABLE daily_activity ADD COLUMN {col} {defn}")
        conn.commit()

    def upsert(self, records: Iterable[ActivityRecord]) -> int:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        conn = self.connect()
        try:
            count = 0
            for rec in records:
                conn.execute(
                    _UPSERT,
                    (
                        rec.date, rec.source, rec.model, rec.scope,
                        rec.sessions, rec.prompts, rec.turns, rec.tool_calls,
                        rec.context_peak_tokens,
                        rec.input_tokens, rec.output_tokens,
                        rec.cache_read_tokens, rec.cache_write_tokens,
                        rec.reasoning_tokens,
                        rec.first_ts, rec.last_ts, now,
                    ),
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()
