from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from src.collectors.copilot_cli import CopilotCliCollector


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "copilot"
    home.mkdir()
    db_path = home / "session-store.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            cwd TEXT,
            repository TEXT,
            origin TEXT,
            branch TEXT,
            status TEXT,
            startedAt TEXT,
            endedAt TEXT
        )
    """)
    conn.commit()
    conn.close()
    return home


def _add_session(home: Path, id_: str, cwd: str, repo: str,
                 started: str, ended: str) -> None:
    conn = sqlite3.connect(home / "session-store.db")
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, 'github', 'main', 'sum', ?, ?)",
        (id_, cwd, repo, started, ended),
    )
    conn.commit()
    conn.close()


def _write_events(home: Path, session_id: str, events: list[dict]) -> None:
    state_dir = home / "session-state" / session_id
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


def _shutdown(model_metrics: dict) -> dict:
    return {
        "type": "session.shutdown",
        "modelMetrics": {
            m: {
                "sessions": 1 if i == 0 else 0,
                "turns": v.get("turns", 0),
                "inputTokens": v.get("input", 0),
                "outputTokens": v.get("output", 0),
                "cacheReadTokens": v.get("cache_read", 0),
                "cacheWriteTokens": v.get("cache_write", 0),
                "reasoningTokens": v.get("reasoning", 0),
            }
            for i, (m, v) in enumerate(model_metrics.items())
        },
    }


def test_basic_shutdown_session(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/myapp", "owner/myapp",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 3, "input": 1000, "output": 200,
                                          "cache_read": 500, "cache_write": 100}}),
    ])
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))
    assert len(records) == 1
    r = records[0]
    assert r.session_id == "s1"
    assert r.source == "copilot_cli"
    assert r.model == "claude-sonnet-4-6"
    assert r.turns == 3
    assert r.input_tokens == 1000
    assert r.cache_read_tokens == 500
    assert r.cache_creation_tokens == 100


def test_multi_model_shutdown_yields_separate_records(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _shutdown({
            "claude-opus-4-8": {"turns": 2, "input": 10000, "output": 500},
            "claude-sonnet-4-6": {"turns": 0, "input": 3000, "output": 100},
        }),
    ])
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))
    assert len(records) == 2
    models = {r.model for r in records}
    assert models == {"claude-opus-4-8", "claude-sonnet-4-6"}


def test_since_filter(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s-old", "/work/x", "", "2026-06-01T10:00:00.000Z", "2026-06-01T11:00:00.000Z")
    _write_events(home, "s-old", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))
    assert records == []


def test_project_from_repository(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/myrepo",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    r = list(CopilotCliCollector(home, track_project_names=True).collect(date(2026, 6, 10)))[0]
    assert r.project == "myrepo"


def test_project_from_cwd_when_no_repo(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/localproject", "",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    r = list(CopilotCliCollector(home, track_project_names=True).collect(date(2026, 6, 10)))[0]
    assert r.project == "localproject"


def test_project_none_when_disabled(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/secret", "owner/secret",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    r = list(CopilotCliCollector(home, track_project_names=False).collect(date(2026, 6, 10)))[0]
    assert r.project is None
