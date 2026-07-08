"""Local-only project identity mapping: cwd -> guid -> whimsical name.

The ``project_identities`` table lives in the same SQLite file as the
session store but is intentionally invisible to the sync machinery — it is
never pushed to remote stores.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .whimsy import generate_name

_CREATE_IDENTITIES = """
CREATE TABLE IF NOT EXISTS project_identities (
    cwd_key         TEXT PRIMARY KEY,
    guid            TEXT NOT NULL UNIQUE,
    whimsical_name  TEXT UNIQUE,
    created_at      TEXT NOT NULL
)
"""

_GUID_LENGTH = 12


def _normalize(cwd: str) -> str:
    """Case-insensitive identity key for a working directory."""
    return cwd.strip().casefold()


class ProjectIdentityStore:
    """Persists stable per-project identities keyed by normalized cwd."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        with closing(self._connect()) as conn, conn:
            conn.execute(_CREATE_IDENTITIES)

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._db_path)

    def resolve_guid(self, cwd: str | None) -> str | None:
        """Return the stable guid for *cwd*, creating one on first sight."""
        if not cwd or not cwd.strip():
            return None
        key = _normalize(cwd)
        with closing(self._connect()) as conn, conn:
            guid = uuid.uuid4().hex[:_GUID_LENGTH]
            conn.execute(
                "INSERT INTO project_identities (cwd_key, guid, created_at) "
                "VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(cwd_key) DO NOTHING",
                (key, guid),
            )
            row = conn.execute(
                "SELECT guid FROM project_identities WHERE cwd_key = ?", (key,)
            ).fetchone()
            return row[0] if row is not None else None

    def resolve_whimsical(self, cwd: str | None) -> str | None:
        """Return the stable whimsical name for *cwd*, creating one on first sight."""
        guid = self.resolve_guid(cwd)
        if guid is None:
            return None
        with closing(self._connect()) as conn, conn:
            for _ in range(2):
                row = conn.execute(
                    "SELECT whimsical_name FROM project_identities WHERE guid = ?",
                    (guid,),
                ).fetchone()
                if row is not None and row[0]:
                    return row[0]
                taken = {
                    r[0]
                    for r in conn.execute(
                        "SELECT whimsical_name FROM project_identities "
                        "WHERE whimsical_name IS NOT NULL"
                    )
                }
                name = generate_name(taken)
                try:
                    conn.execute(
                        "UPDATE project_identities SET whimsical_name = ? "
                        "WHERE guid = ? AND whimsical_name IS NULL",
                        (name, guid),
                    )
                except sqlite3.IntegrityError:
                    pass
                row = conn.execute(
                    "SELECT whimsical_name FROM project_identities WHERE guid = ?",
                    (guid,),
                ).fetchone()
                if row is not None and row[0]:
                    return row[0]
            return None

    def close(self) -> None:
        """Connections are per-call context managers; nothing to release."""
