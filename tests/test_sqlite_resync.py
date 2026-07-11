from __future__ import annotations

from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


def _rec(session_id: str, **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-haiku-4-5-20251001", date="2026-07-01", turns=1)
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def test_upsert_with_changed_field_clears_sync_log(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", turns=1)])
    store.mark_synced([_rec("s1", turns=1)], "supabase")
    assert store.unsynced_for("supabase") == []

    # Re-collect with a changed field (simulates a backfill run)
    store.upsert([_rec("s1", turns=2)])

    assert len(store.unsynced_for("supabase")) == 1


def test_upsert_with_changed_canonical_model_clears_sync_log(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", canonical_model=None)])
    store.mark_synced([_rec("s1", canonical_model=None)], "supabase")
    assert store.unsynced_for("supabase") == []

    store.upsert([_rec("s1", canonical_model="claude-haiku-4-5")])

    unsynced = store.unsynced_for("supabase")
    assert len(unsynced) == 1
    assert unsynced[0].canonical_model == "claude-haiku-4-5"


def test_upsert_with_unchanged_values_does_not_clear_sync_log(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", turns=1)])
    store.mark_synced([_rec("s1", turns=1)], "supabase")
    assert store.unsynced_for("supabase") == []

    # Re-collect with identical values — nothing actually changed
    store.upsert([_rec("s1", turns=1)])

    assert store.unsynced_for("supabase") == []


def test_upsert_first_insert_does_not_touch_sync_log(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1")])
    # No prior sync_log entry existed — nothing to clear, no error
    assert len(store.unsynced_for("supabase")) == 1
