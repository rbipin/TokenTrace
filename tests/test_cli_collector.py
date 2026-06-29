"""Tests for CopilotCliCollector using a fixture ~/.copilot tree."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from aitoken.collectors import CopilotCliCollector


def _make_session_store(home: Path) -> None:
    db = home / "session-store.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, repository TEXT,
            host_type TEXT, branch TEXT, summary TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE turns (id TEXT PRIMARY KEY, session_id TEXT, turn_index INT,
            user_message TEXT, assistant_response TEXT, timestamp TEXT);
        """
    )
    conn.execute("INSERT INTO sessions VALUES ('s1','C:/x','owner/repoA','github','main','sum','2026-06-10T12:00:00.000Z','2026-06-10T12:30:00.000Z')")
    conn.execute("INSERT INTO sessions VALUES ('s2','C:/y','','github','main','sum','2026-06-10T13:00:00.000Z','2026-06-10T13:30:00.000Z')")
    conn.executemany(
        "INSERT INTO turns VALUES (?,?,?,?,?,?)",
        [
            ("t1", "s1", 0, "u", "a", "2026-06-10T12:00:00.000Z"),
            ("t2", "s1", 1, "u", "a", "2026-06-10T12:05:00.000Z"),
            ("t3", "s2", 0, "u", "a", "2026-06-10T13:00:00.000Z"),
            ("told", "s1", 2, "u", "a", "2023-01-01T00:00:00.000Z"),  # before since
        ],
    )
    conn.commit()
    conn.close()


def _make_log(home: Path) -> None:
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "process-1.log").write_text(
        "2026-06-10T12:01:00.000Z [INFO] CompactionProcessor: Utilization 46.1% (92263/200000 tokens) below threshold 80%\n"
        "2026-06-10T12:02:00.000Z [INFO] CompactionProcessor: Utilization 47.6% (95163/200000 tokens) below threshold 80%\n"
        "2026-06-10T12:03:00.000Z [INFO] unrelated line without tokens\n",
        encoding="utf-8",
    )


def test_collects_sessions_turns_and_scope(tmp_path: Path):
    home = tmp_path / ".copilot"
    home.mkdir()
    _make_session_store(home)
    _make_log(home)

    records = list(CopilotCliCollector(home).collect(date(2026, 6, 1)))
    by_scope = {r.scope: r for r in records}

    # repoA scope: 1 session, 2 turns/prompts
    assert by_scope["owner/repoA"].sessions == 1
    assert by_scope["owner/repoA"].prompts == 2
    assert by_scope["owner/repoA"].turns == 2
    # cwd fallback when repository empty
    assert by_scope["C:/y"].sessions == 1
    # context peak folded into most-active row (owner/repoA has 2 prompts > C:/y with 1)
    assert by_scope["owner/repoA"].context_peak_tokens == 95163
    assert "" not in by_scope
    assert all(r.source == "copilot-cli" for r in records)


def test_since_excludes_old_turns(tmp_path: Path):
    home = tmp_path / ".copilot"
    home.mkdir()
    _make_session_store(home)
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 1)))
    # the 2023 turn must not inflate counts
    total_turns = sum(r.turns for r in records)
    assert total_turns == 3


def test_model_resolved_from_events_jsonl(tmp_path: Path):
    import json
    home = tmp_path / ".copilot"
    home.mkdir()
    _make_session_store(home)
    # Write events.jsonl for session s1 with claude-opus-4.7 as dominant model.
    ss = home / "session-state" / "s1"
    ss.mkdir(parents=True)
    events = [
        {"type": "assistant.message", "data": {"model": "claude-opus-4.7", "content": "hi"}},
        {"type": "assistant.message", "data": {"model": "claude-opus-4.7", "content": "there"}},
        {"type": "assistant.message", "data": {"model": "claude-sonnet-4", "content": "ok"}},
    ]
    (ss / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 1)))
    # s1 scope is owner/repoA; model should be claude-opus-4.7 (plurality)
    repoA = next(r for r in records if r.scope == "owner/repoA")
    assert repoA.model == "claude-opus-4.7"


def test_shutdown_tokens_per_model(tmp_path: Path):
    import json
    home = tmp_path / ".copilot"
    home.mkdir()
    _make_session_store(home)
    ss = home / "session-state" / "s1"
    ss.mkdir(parents=True)
    shutdown_event = {
        "type": "session.shutdown",
        "timestamp": "2026-06-10T12:30:00.000Z",
        "data": {
            "currentModel": "claude-opus-4.7",
            "modelMetrics": {
                "claude-opus-4.7": {
                    "requests": {"count": 2, "cost": 2},
                    "usage": {
                        "inputTokens": 10000,
                        "outputTokens": 500,
                        "cacheReadTokens": 8000,
                        "cacheWriteTokens": 1000,
                        "reasoningTokens": 50,
                    },
                },
                "claude-sonnet-4.6": {
                    "requests": {"count": 1, "cost": 0},
                    "usage": {
                        "inputTokens": 3000,
                        "outputTokens": 100,
                        "cacheReadTokens": 2000,
                        "cacheWriteTokens": 500,
                        "reasoningTokens": 0,
                    },
                },
            },
        },
    }
    (ss / "events.jsonl").write_text(json.dumps(shutdown_event), encoding="utf-8")

    records = list(CopilotCliCollector(home).collect(date(2026, 6, 1)))
    by_model = {r.model: r for r in records if r.scope == "owner/repoA"}

    # Primary model (most output tokens) gets sessions/turns
    opus = by_model["claude-opus-4.7"]
    assert opus.input_tokens == 10000
    assert opus.output_tokens == 500
    assert opus.cache_read_tokens == 8000
    assert opus.cache_write_tokens == 1000
    assert opus.reasoning_tokens == 50
    assert opus.sessions == 1
    assert opus.turns == 2  # 2 turns for s1 within since window

    # Secondary model gets token counts but no sessions/turns
    sonnet = by_model["claude-sonnet-4.6"]
    assert sonnet.input_tokens == 3000
    assert sonnet.output_tokens == 100
    assert sonnet.sessions == 0
    assert sonnet.turns == 0


def test_active_session_output_tokens_fallback(tmp_path: Path):
    """Without session.shutdown, outputTokens from assistant.message are summed."""
    import json
    home = tmp_path / ".copilot"
    home.mkdir()
    _make_session_store(home)
    ss = home / "session-state" / "s1"
    ss.mkdir(parents=True)
    events = [
        {"type": "assistant.message", "data": {"model": "claude-opus-4.7", "outputTokens": 300, "content": "hi"}},
        {"type": "assistant.message", "data": {"model": "claude-opus-4.7", "outputTokens": 200, "content": "there"}},
    ]
    (ss / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 1)))
    repoA = next(r for r in records if r.scope == "owner/repoA")
    assert repoA.output_tokens == 500
    assert repoA.input_tokens == 0  # not available without shutdown


def test_missing_home_is_safe(tmp_path: Path):
    assert list(CopilotCliCollector(tmp_path / "nope").collect(date(2026, 1, 1))) == []
