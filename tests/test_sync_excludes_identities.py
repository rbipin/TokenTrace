"""Regression guard: project_identities is local-only and never syncs."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src.models import SessionRecord
from src.project_identity import ProjectIdentityStore
from src.stores.sqlite import SqliteStore


class _RecordingRemote:
    def __init__(self) -> None:
        self.name = "supabase"
        self.pushed: list[SessionRecord] = []

    def upsert(self, records: list[SessionRecord]) -> int:
        self.pushed.extend(records)
        return len(records)

    def close(self) -> None:
        pass


def _seed_session(db: Path) -> SessionRecord:
    store = SqliteStore(db)
    record = SessionRecord(
        session_id="s1",
        source="copilot_cli",
        model="m",
        date="2026-07-08",
        project="visible-project",
    )
    store.upsert([record])
    return record


def test_identity_rows_do_not_enter_sync_payloads(tmp_path):
    db = tmp_path / "usage.db"
    identity_store = ProjectIdentityStore(db)
    secret_name = identity_store.resolve_whimsical("/work/secret-project")
    seeded = _seed_session(db)

    sqlite = SqliteStore(db)
    remote = _RecordingRemote()

    from src.commands.common import run_sync

    result = run_sync(sqlite, [remote], dry_run=False)

    assert result == {"supabase": {"pushed": 1, "failed": False}}
    assert remote.pushed == [seeded]
    assert remote.pushed[0].project == "visible-project"
    assert all(
        secret_name not in (record.project or "") for record in remote.pushed
    )
    assert all(record.session_id == "s1" for record in remote.pushed)
    assert sqlite.unsynced_for("supabase") == []

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_identities").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sync_log").fetchone()[0] == 1

    identity_store.close()
    sqlite.close()

