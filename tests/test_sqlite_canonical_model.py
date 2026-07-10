from __future__ import annotations

import sqlite3

from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


def _rec(session_id: str, **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-haiku-4-5-20251001", date="2026-07-01")
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def test_canonical_model_defaults_to_none():
    rec = _rec("s1")
    assert rec.canonical_model is None


def test_canonical_model_persisted_and_read_back(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", canonical_model="claude-haiku-4-5")])
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT canonical_model FROM sessions WHERE session_id='s1'"
    ).fetchone()
    assert row[0] == "claude-haiku-4-5"


def test_canonical_model_null_when_not_set(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1")])
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT canonical_model FROM sessions WHERE session_id='s1'"
    ).fetchone()
    assert row[0] is None


def test_unsynced_for_round_trips_canonical_model(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", canonical_model="claude-haiku-4-5")])
    [rec] = store.unsynced_for("supabase")
    assert rec.canonical_model == "claude-haiku-4-5"


def test_migration_adds_canonical_model_to_existing_db(tmp_db):
    # Simulate a pre-existing DB created before this column existed.
    conn = sqlite3.connect(tmp_db)
    conn.execute("""
        CREATE TABLE sessions (
            session_id TEXT NOT NULL, source TEXT NOT NULL, model TEXT NOT NULL,
            date TEXT NOT NULL, start_ts TEXT, end_ts TEXT, project TEXT,
            turns INTEGER DEFAULT 0, tool_calls INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
            context_peak_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
            context TEXT DEFAULT 'personal',
            PRIMARY KEY (session_id, source, model)
        )
    """)
    conn.commit()
    conn.close()

    SqliteStore(tmp_db)  # triggers _migrate()

    conn2 = sqlite3.connect(tmp_db)
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "canonical_model" in cols
