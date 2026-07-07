"""Tests for the work/personal usage-context label."""
from __future__ import annotations

import sqlite3
from datetime import date

from src.config import Config, write_toml_setting
from src.models import SessionRecord
from src.pipeline import TrackerPipeline
from src.stores.sqlite import SqliteStore


def _record(**overrides) -> SessionRecord:
    base = dict(
        session_id="s1", source="copilot-cli", model="m1",
        date="2026-01-01", input_tokens=10, output_tokens=5,
    )
    base.update(overrides)
    return SessionRecord(**base)


# --- config ---

def test_config_default_context_is_personal():
    assert Config().context == "personal"


def test_load_reads_context_from_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text('[tracking]\ncontext = "work"\n')
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    assert Config.load().context == "work"


def test_write_toml_setting_string_value(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    write_toml_setting("context", "work")
    assert 'context = "work"' in toml.read_text()


def test_write_toml_setting_preserves_bool_and_string(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = true\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    write_toml_setting("context", "work")
    content = toml.read_text()
    assert "track_project_names = true" in content
    assert 'context = "work"' in content


# --- model ---

def test_record_default_context_is_personal():
    assert _record().context == "personal"


# --- sqlite store ---

def test_sqlite_round_trips_context(tmp_path):
    db = tmp_path / "usage.db"
    store = SqliteStore(db)
    store.upsert([_record(context="work")])
    row = sqlite3.connect(db).execute(
        "SELECT context FROM sessions WHERE session_id = 's1'"
    ).fetchone()
    assert row[0] == "work"
    unsynced = store.unsynced_for("supabase")
    assert unsynced[0].context == "work"


def test_sqlite_migrates_existing_table_without_context(tmp_path):
    db = tmp_path / "usage.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE sessions (
            session_id TEXT NOT NULL, source TEXT NOT NULL, model TEXT NOT NULL,
            date TEXT NOT NULL, start_ts TEXT, end_ts TEXT, project TEXT,
            turns INTEGER DEFAULT 0, tool_calls INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
            context_peak_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
            PRIMARY KEY (session_id, source, model))"""
    )
    conn.execute(
        "INSERT INTO sessions (session_id, source, model, date) VALUES ('old', 'x', 'm', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    store = SqliteStore(db)  # triggers migration
    recs = store.unsynced_for("supabase")
    assert recs[0].context == "personal"


# --- pipeline ---

class _StubCollector:
    source = "stub"

    def collect(self, since):
        return [_record()]


def test_pipeline_stamps_context_on_records(tmp_path):
    db = tmp_path / "usage.db"
    result = (
        TrackerPipeline()
        .context("work")
        .add(_StubCollector())
        .since(date(2026, 1, 1))
        .stores(SqliteStore(db))
        .run()
    )
    assert result.records_written == 1
    row = sqlite3.connect(db).execute("SELECT context FROM sessions").fetchone()
    assert row[0] == "work"


def test_pipeline_default_context_is_personal(tmp_path):
    db = tmp_path / "usage.db"
    (
        TrackerPipeline()
        .add(_StubCollector())
        .since(date(2026, 1, 1))
        .stores(SqliteStore(db))
        .run()
    )
    row = sqlite3.connect(db).execute("SELECT context FROM sessions").fetchone()
    assert row[0] == "personal"
