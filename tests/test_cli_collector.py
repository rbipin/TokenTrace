from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from src.collectors.copilot_cli import CopilotCliCollector
from src.project_identity import ProjectNameResolver


class StubResolver:
    """Records inputs; returns a canned value."""

    def __init__(self, result="RESOLVED"):
        self.result = result
        self.calls: list[tuple[str | None, str | None]] = []

    def resolve(self, display_name, cwd):
        self.calls.append((display_name, cwd))
        return self.result


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


def test_project_inputs_prefer_repository(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/myrepo",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    stub = StubResolver()
    r = list(CopilotCliCollector(home, resolver=stub).collect(date(2026, 6, 10)))[0]
    assert r.project == "RESOLVED"
    assert stub.calls == [("owner/myrepo", "owner/myrepo")]


def test_project_inputs_fall_back_to_cwd_basename(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/localproject", "",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    stub = StubResolver()
    list(CopilotCliCollector(home, resolver=stub).collect(date(2026, 6, 10)))
    assert stub.calls == [("localproject", "localproject")]


def test_project_none_without_resolver(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/secret", "owner/secret",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.project is None


def test_end_to_end_with_real_resolver_yes_mode(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/myrepo",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    resolver = ProjectNameResolver("yes")
    r = list(CopilotCliCollector(home, resolver=resolver).collect(date(2026, 6, 10)))[0]
    assert r.project == "owner/myrepo"


def test_project_slug_resolved_from_cwd_git_config(tmp_path):
    home = _make_home(tmp_path)
    repo_dir = tmp_path / "checkout"
    git = repo_dir / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text(
        '[remote "origin"]\n\turl = git@github.com:acme/widgets.git\n',
        encoding="utf-8",
    )
    _add_session(home, "s1", str(repo_dir), "",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    stub = StubResolver()
    list(CopilotCliCollector(home, resolver=stub).collect(date(2026, 6, 10)))
    assert stub.calls == [("acme/widgets", "acme/widgets")]


def _tool_event(model: str | None = None) -> dict:
    e: dict = {"type": "tool.execution_complete"}
    if model:
        e["model"] = model
    return e


def test_tool_calls_counted_per_model_with_shutdown(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _tool_event("model-a"),
        _tool_event("model-a"),
        _tool_event("model-b"),
        _shutdown({
            "model-a": {"turns": 2, "input": 100, "output": 10},
            "model-b": {"turns": 1, "input": 50, "output": 5},
        }),
    ])
    records = {r.model: r for r in CopilotCliCollector(home).collect(date(2026, 6, 10))}
    assert records["model-a"].tool_calls == 2
    assert records["model-b"].tool_calls == 1


def test_tool_calls_summed_in_fallback_without_shutdown(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        {"type": "assistant.message", "model": "model-a",
         "usage": {"inputTokens": 10, "outputTokens": 5}},
        _tool_event("model-a"),
        _tool_event(),  # no model field — still counted in the session total
    ])
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))
    assert len(records) == 1
    assert records[0].tool_calls == 2


def test_tool_calls_zero_when_no_tool_events(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"model-a": {"turns": 1, "input": 10, "output": 1}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.tool_calls == 0


def _add_usage_events(home: Path, rows: list[tuple]) -> None:
    """rows: (session_id, agent_id, model, input_tokens, output_tokens)"""
    conn = sqlite3.connect(home / "session-store.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_usage_events (
            session_id TEXT, turn_index INTEGER, agent_id TEXT, model TEXT,
            input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER,
            reasoning_tokens INTEGER
        )
    """)
    conn.executemany(
        "INSERT INTO assistant_usage_events VALUES (?, 0, ?, ?, ?, ?, 0, 0, 0)",
        rows,
    )
    conn.commit()
    conn.close()


def test_context_peak_from_usage_events(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _add_usage_events(home, [
        ("s1", None, "model-a", 1000, 50),   # footprint 1050
        ("s1", None, "model-a", 2000, 100),  # footprint 2100 <- peak
        ("s1", None, "model-a", 500, 20),
    ])
    _write_events(home, "s1", [
        _shutdown({"model-a": {"turns": 3, "input": 3500, "output": 170}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.context_peak_tokens == 2100


def test_context_peak_excludes_subagent_rows(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _add_usage_events(home, [
        ("s1", None, "model-a", 1000, 50),          # main: 1050 <- peak
        ("s1", "agent-123", "model-a", 9000, 900),  # subagent: excluded
    ])
    _write_events(home, "s1", [
        _shutdown({"model-a": {"turns": 1, "input": 1000, "output": 50}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.context_peak_tokens == 1050


def test_context_peak_zero_when_table_missing(tmp_path):
    home = _make_home(tmp_path)  # fixture db has no assistant_usage_events table
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"model-a": {"turns": 1, "input": 10, "output": 1}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.context_peak_tokens == 0


def test_context_peak_in_fallback_path(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _add_usage_events(home, [("s1", None, "model-a", 700, 30)])
    _write_events(home, "s1", [
        {"type": "assistant.message", "model": "model-a",
         "usage": {"inputTokens": 700, "outputTokens": 30}},
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.context_peak_tokens == 730


def test_tool_event_after_shutdown_still_counted(tmp_path):
    """Regression guard: the scan must not short-circuit on session.shutdown."""
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _tool_event("model-a"),
        _shutdown({"model-a": {"turns": 1, "input": 10, "output": 1}}),
        _tool_event("model-a"),  # after shutdown — must still be counted
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.tool_calls == 2
