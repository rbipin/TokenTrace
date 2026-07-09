"""Local-only project identity mapping: project key -> guid -> whimsical name.

Project keys are repo slugs (``owner/repo``) or cwd folder names — full
paths are no longer stored. Legacy full-path keys are migrated in place on
startup (guids and whimsical names preserved; clones of the same repo are
merged, keeping the oldest row).

The ``project_identities`` table lives in the same SQLite file as the
session store but is intentionally invisible to the sync machinery — it is
never pushed to remote stores.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
import sys

from .whimsy import generate_name
from .repo_identity import resolve_repo_slug

_CREATE_IDENTITIES = """
CREATE TABLE IF NOT EXISTS project_identities (
    cwd_key         TEXT PRIMARY KEY,
    guid            TEXT NOT NULL UNIQUE,
    whimsical_name  TEXT UNIQUE,
    created_at      TEXT NOT NULL
)
"""

_GUID_LENGTH = 12
PROJECT_NAME_MODES = ("yes", "no", "whimsical")


def _normalize(cwd: str) -> str:
    """Case-insensitive identity key for a working directory."""
    return cwd.strip().casefold()


_PATH_SEGMENT_RE = re.compile(r"[\\/]+")


def _looks_like_path(key: str) -> bool:
    """True for legacy full-path keys; False for slug or folder-name keys.

    Slug keys contain exactly one interior ``/`` (``owner/repo``); folder
    names contain no separators. Paths contain ``\\``, ``:``, a leading
    ``/``, or more than one ``/``.
    """
    return (
        "\\" in key
        or ":" in key
        or key.startswith("/")
        or key.count("/") > 1
    )


def _folder_name(path_str: str) -> str:
    segments = [s for s in _PATH_SEGMENT_RE.split(path_str) if s]
    return segments[-1] if segments else path_str


def _migrated_key(old_key: str) -> str:
    slug = resolve_repo_slug(old_key)
    if slug:
        return _normalize(slug)
    return _normalize(_folder_name(old_key))


class ProjectIdentityStore:
    """Persists stable per-project identities keyed by normalized cwd."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        with closing(self._connect()) as conn, conn:
            conn.execute(_CREATE_IDENTITIES)
            try:
                self._migrate_path_keys(conn)
            except Exception as exc:
                print(
                    f"Warning [project-identity]: key migration failed: {exc}; "
                    "existing identities left unchanged",
                    file=sys.stderr,
                )

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._db_path)

    @staticmethod
    def _migrate_path_keys(conn: sqlite3.Connection) -> None:
        """One-time re-key of legacy full-path rows to project keys."""
        rows = conn.execute(
            "SELECT cwd_key, created_at FROM project_identities"
        ).fetchall()
        for old_key, created_at in rows:
            if not _looks_like_path(old_key):
                continue
            new_key = _migrated_key(old_key)
            existing = conn.execute(
                "SELECT created_at FROM project_identities WHERE cwd_key = ?",
                (new_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "UPDATE project_identities SET cwd_key = ? WHERE cwd_key = ?",
                    (new_key, old_key),
                )
            elif existing[0] <= created_at:
                # A row already owns the new key and is older — drop this one.
                conn.execute(
                    "DELETE FROM project_identities WHERE cwd_key = ?", (old_key,)
                )
            else:
                # This row is older — it wins the key.
                conn.execute(
                    "DELETE FROM project_identities WHERE cwd_key = ?", (new_key,)
                )
                conn.execute(
                    "UPDATE project_identities SET cwd_key = ? WHERE cwd_key = ?",
                    (new_key, old_key),
                )

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

    def list_identities(self) -> list[dict]:
        """Return all identity mappings, sorted by cwd_key."""
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT cwd_key, guid, whimsical_name, created_at "
                "FROM project_identities ORDER BY cwd_key"
            ).fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        """Connections are per-call context managers; nothing to release."""


class ProjectNameResolver:
    """Owns the tri-state project naming policy shared by all collectors.

    Collectors supply source-specific raw inputs (display name, cwd); the
    resolver decides what — if anything — goes into ``SessionRecord.project``.
    """

    def __init__(
        self, mode: str, identity_store: ProjectIdentityStore | None = None
    ) -> None:
        if mode not in PROJECT_NAME_MODES:
            raise ValueError(
                f"invalid project name mode {mode!r}; "
                f"expected one of {', '.join(PROJECT_NAME_MODES)}"
            )
        if mode in ("no", "whimsical") and identity_store is None:
            raise ValueError(f"mode {mode!r} requires an identity_store")
        self._mode = mode
        self._identity_store = identity_store
        self._warned = False

    def resolve(self, display_name: str | None, cwd: str | None) -> str | None:
        """Resolve the project value for one session record."""
        if self._mode == "yes":
            return display_name
        try:
            if self._mode == "no":
                return self._identity_store.resolve_guid(cwd)
            return self._identity_store.resolve_whimsical(cwd)
        except Exception as exc:
            if not self._warned:
                print(
                    f"Warning [project-identity]: {exc}; "
                    "project names will be omitted",
                    file=sys.stderr,
                )
                self._warned = True
            return None
