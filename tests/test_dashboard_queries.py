from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from src.dashboard import queries
from src.stores.sqlite import SqliteStore
from src.models import SessionRecord


def _rec(session_id: str, date_str: str, **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-sonnet-4-6", date=date_str)
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def _conn(tmp_db) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    return conn


def test_date_filter_known_periods():
    for period in ("all", "day", "week", "month", "year"):
        where, params = queries.date_filter(period, None, None)
        assert isinstance(where, str) and where
        assert params == []


def test_date_filter_custom_requires_bounds():
    with pytest.raises(ValueError):
        queries.date_filter("custom", None, None)
    where, params = queries.date_filter("custom", "2026-01-01", "2026-01-31")
    assert params == ["2026-01-01", "2026-01-31"]


def test_date_filter_unknown_period():
    with pytest.raises(ValueError):
        queries.date_filter("bogus", None, None)


def test_summary_totals_and_harnesses(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=100, output_tokens=50, source="claude_cli"),
        _rec("s2", today, input_tokens=200, output_tokens=0, source="copilot_cli",
             model="gpt-5"),
    ])
    result = queries.summary(_conn(tmp_db), "all")
    assert result["total_tokens"] == 350
    assert result["session_count"] == 2
    sources = {h["source"] for h in result["harnesses"]}
    assert sources == {"claude_cli", "copilot_cli"}
    claude = next(h for h in result["harnesses"] if h["source"] == "claude_cli")
    assert claude["tokens"] == 150
    assert round(claude["pct"], 4) == round(150 / 350, 4)


def test_summary_empty_db_returns_zeros(tmp_db):
    SqliteStore(tmp_db)  # creates schema, no rows
    result = queries.summary(_conn(tmp_db), "all")
    assert result["total_tokens"] == 0
    assert result["harnesses"] == []
    assert result["models"] == []


def test_summary_project_filter(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=100, project="proj-a"),
        _rec("s2", today, input_tokens=999, project="proj-b"),
    ])
    result = queries.summary(_conn(tmp_db), "all", project="proj-a")
    assert result["total_tokens"] == 100


def test_heatmap_returns_daily_totals(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([_rec("s1", today, input_tokens=100)])
    result = queries.heatmap(_conn(tmp_db), days=180)
    assert result == [{"date": today, "tokens": 100}]


def test_trend_breaks_down_by_source(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=100, source="claude_cli"),
        _rec("s2", today, input_tokens=40, source="copilot_cli", model="gpt-5"),
    ])
    result = queries.trend(_conn(tmp_db), days=30)
    by_source = {r["source"]: r["tokens"] for r in result}
    assert by_source == {"claude_cli": 100, "copilot_cli": 40}


def test_projects_sorted_descending(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=50, project="small"),
        _rec("s2", today, input_tokens=500, project="big"),
        _rec("s3", today, input_tokens=10),  # no project, excluded
    ])
    result = queries.projects(_conn(tmp_db), "all")
    assert [r["project"] for r in result] == ["big", "small"]


def test_project_detail_scopes_to_one_project(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=100, project="proj-a"),
        _rec("s2", today, input_tokens=999, project="proj-b"),
    ])
    result = queries.project_detail(_conn(tmp_db), "proj-a", "all")
    assert result["total_tokens"] == 100


def test_sync_status_groups_by_store(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([_rec("s1", today, input_tokens=10)])
    store.mark_synced([_rec("s1", today, input_tokens=10)], "supabase")
    result = queries.sync_status(_conn(tmp_db))
    assert len(result["stores"]) == 1
    assert result["stores"][0]["name"] == "supabase"
    assert result["stores"][0]["last_synced_at"] is not None


def test_sync_status_empty(tmp_db):
    SqliteStore(tmp_db)
    result = queries.sync_status(_conn(tmp_db))
    assert result == {"last_collected_at": None, "stores": []}


def test_sync_status_includes_last_collected_at(tmp_db):
    store = SqliteStore(tmp_db)
    store.record_run("2026-07-19T10:00:00+00:00")
    result = queries.sync_status(_conn(tmp_db))
    assert result["last_collected_at"] == "2026-07-19T10:00:00+00:00"


def test_meta_most_recent_data(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([_rec("s1", today, end_ts="2026-07-19T10:00:00+00:00")])
    result = queries.meta(_conn(tmp_db))
    assert result["most_recent_data_at"] == "2026-07-19T10:00:00+00:00"


def test_meta_empty_db(tmp_db):
    SqliteStore(tmp_db)
    result = queries.meta(_conn(tmp_db))
    assert result["most_recent_data_at"] is None
