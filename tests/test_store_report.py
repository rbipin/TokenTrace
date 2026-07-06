from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


def _rec(session_id: str, date_str: str = "2026-07-01", **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-sonnet-4-6", date=date_str)
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def test_upsert_and_count(tmp_db):
    store = SqliteStore(tmp_db)
    n = store.upsert([_rec("s1"), _rec("s2")])
    assert n == 2


def test_upsert_is_idempotent(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", input_tokens=100)])
    store.upsert([_rec("s1", input_tokens=200)])  # re-collect same session
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT input_tokens FROM sessions WHERE session_id='s1'").fetchone()
    assert row[0] == 200  # last write wins


def test_upsert_different_models_same_session(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([
        _rec("s1", model="claude-sonnet-4-6", turns=3),
        _rec("s1", model="claude-opus-4-8", turns=1),
    ])
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT model FROM sessions WHERE session_id='s1' ORDER BY model").fetchall()
    assert len(rows) == 2


def test_migration_drops_old_usage_table(tmp_db, capsys):
    import sqlite3
    # Create old-style usage table
    conn = sqlite3.connect(tmp_db)
    conn.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    # Init store — should migrate
    SqliteStore(tmp_db)
    captured = capsys.readouterr()
    assert "usage" in captured.err  # migration warning printed
    conn2 = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "sessions" in tables
    assert "usage" not in tables


def test_project_stored_when_set(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", project="MyApp")])
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT project FROM sessions WHERE session_id='s1'").fetchone()
    assert row[0] == "MyApp"


def test_project_null_when_not_set(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1")])
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT project FROM sessions WHERE session_id='s1'").fetchone()
    assert row[0] is None


# ── Report tests ─────────────────────────────────────────────────────────────

from src.report import UsageReporter


def _populate(db: Path) -> None:
    store = SqliteStore(db)
    store.upsert([
        SessionRecord(
            session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
            date="2026-07-01", turns=3,
            input_tokens=1000, output_tokens=200,
            cache_creation_tokens=500, cache_read_tokens=4000,
        ),
        SessionRecord(
            session_id="s2", source="claude_cli", model="claude-sonnet-4-6",
            date="2026-07-02", turns=2,
            input_tokens=800, output_tokens=150,
            cache_creation_tokens=200, cache_read_tokens=2000,
            project="myapp",
        ),
        SessionRecord(
            session_id="s3", source="claude_cli", model="claude-sonnet-4-6",
            date="2026-06-30", turns=4,
            input_tokens=500, output_tokens=100,
            cache_creation_tokens=0, cache_read_tokens=0,
        ),
    ])


def test_report_day_returns_string(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day")
    assert isinstance(output, str)
    assert len(output) > 0


def test_report_includes_cache_efficiency_header(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day")
    assert "Cache efficiency" in output


def test_report_month(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="month")
    # Detailed view shows project names and column headers
    assert "myapp" in output
    assert "CacheCreate" in output


def test_report_by_project_excludes_null_projects(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="month", by_project=True)
    assert "myapp" in output
    # s1 has no project — its tokens should not appear under a project name
    assert "s1" not in output


def test_report_by_project_shows_note_when_no_projects(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", project=None)])
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day", by_project=True)
    assert "track_project_names" in output


def test_report_default_shows_detailed_columns(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="month")
    # Default view shows full token breakdown columns
    assert "CacheRead" in output
    assert "CacheCreate" in output
    assert "Input" in output


def test_report_summary_month_aggregates(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    # summary + month → aggregated roll-up grouped by period+model
    output = reporter.report(period="month", summary=True)
    assert "Period" in output
    assert "CacheRead" in output


def test_report_summary_month_shows_aggregated(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="month", summary=True)
    # When period is day with summary, shows compact sessions; month shows aggregated
    # Just verify it returns a non-empty string with cache header
    assert "Cache efficiency" in output
    assert len(output) > 0


def test_report_json(tmp_db):
    _populate(tmp_db)
    import json as _json
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day", as_json=True)
    data = _json.loads(output)
    assert "cache_efficiency" in data
    assert "rows" in data
