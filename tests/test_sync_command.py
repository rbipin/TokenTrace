from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
import pytest
from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


class _StubRemote:
    def __init__(self, name, boom=False):
        self.name = name
        self._boom = boom
        self.pushed: list[SessionRecord] = []

    def upsert(self, records):
        if self._boom:
            raise RuntimeError("connection refused")
        self.pushed.extend(records)
        return len(records)

    def close(self):
        pass


def _rec(sid):
    return SessionRecord(session_id=sid, source="claude_cli",
                         model="claude-sonnet-4-6", date="2026-07-01", turns=1)


def _seed(db: Path, *session_ids):
    store = SqliteStore(db)
    store.upsert([_rec(sid) for sid in session_ids])
    return store


def test_sync_pushes_unsynced(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1", "s2")
    remote = _StubRemote("supabase")

    from src.commands.common import run_sync
    result = run_sync(sqlite, [remote], dry_run=False)

    assert result == {"supabase": {"pushed": 2, "failed": False}}
    assert len(remote.pushed) == 2
    assert sqlite.unsynced_for("supabase") == []


def test_sync_dry_run_does_not_push(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1")
    remote = _StubRemote("supabase")

    from src.commands.common import run_sync
    result = run_sync(sqlite, [remote], dry_run=True)

    assert result == {"supabase": {"pending": 1}}
    assert remote.pushed == []
    assert len(sqlite.unsynced_for("supabase")) == 1


def test_sync_remote_failure_leaves_unsynced(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1")
    bad_remote = _StubRemote("cosmos", boom=True)

    from src.commands.common import run_sync
    result = run_sync(sqlite, [bad_remote], dry_run=False)

    assert result["cosmos"]["failed"] is True
    assert len(sqlite.unsynced_for("cosmos")) == 1


def test_sync_already_synced_not_pushed_again(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1", "s2")
    remote = _StubRemote("supabase")
    # Pre-mark s1 as synced
    sqlite.mark_synced([_rec("s1")], "supabase")

    from src.commands.common import run_sync
    run_sync(sqlite, [remote], dry_run=False)

    assert len(remote.pushed) == 1
    assert remote.pushed[0].session_id == "s2"

