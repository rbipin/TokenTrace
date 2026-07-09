from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.collectors.claude_cli import ClaudeCliCollector
from src.models import UNKNOWN_MODEL
from src.project_identity import ProjectNameResolver


class StubResolver:
    def __init__(self, result="RESOLVED"):
        self.result = result
        self.calls: list[tuple[str | None, str | None]] = []

    def resolve(self, display_name, cwd):
        self.calls.append((display_name, cwd))
        return self.result


def _write_session(path: Path, session_id: str, entries: list[dict]) -> None:
    """Write a JSONL session file under path/<project-dir>/<session_id>.jsonl"""
    proj_dir = path / "proj-1"
    proj_dir.mkdir(exist_ok=True)
    (proj_dir / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
    )


def _asst(ts: str, model: str, input_t: int, output_t: int,
          cache_create: int = 0, cache_read: int = 0,
          cwd: str | None = None) -> dict:
    entry: dict = {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cache_creation_input_tokens": cache_create,
                "cache_read_input_tokens": cache_read,
            },
        },
    }
    if cwd:
        entry["cwd"] = cwd
    return entry


def test_single_session_basic(tmp_path):
    _write_session(tmp_path, "sess-abc", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50),
        _asst("2026-07-03T11:00:00.000Z", "claude-sonnet-4-6", 200, 75),
    ])
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))
    assert len(records) == 1
    r = records[0]
    assert r.session_id == "sess-abc"
    assert r.source == "claude_cli"
    assert r.model == "claude-sonnet-4-6"
    assert r.date == "2026-07-03"
    assert r.turns == 2
    assert r.input_tokens == 300
    assert r.output_tokens == 125


def test_start_end_ts(tmp_path):
    _write_session(tmp_path, "sess-ts", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5),
        _asst("2026-07-03T12:30:00.000Z", "claude-sonnet-4-6", 20, 8),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.start_ts is not None
    assert r.end_ts is not None
    assert r.start_ts < r.end_ts


def test_cache_tokens_summed(tmp_path):
    _write_session(tmp_path, "sess-cache", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50, cache_create=200, cache_read=800),
        _asst("2026-07-03T11:00:00.000Z", "claude-sonnet-4-6", 50, 25, cache_create=0, cache_read=400),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.cache_creation_tokens == 200
    assert r.cache_read_tokens == 1200


def test_since_filter_excludes_old_session(tmp_path):
    _write_session(tmp_path, "sess-old", [
        _asst("2026-07-01T10:00:00.000Z", "claude-sonnet-4-6", 100, 50),
    ])
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))
    assert records == []


def test_project_inputs_from_cwd(tmp_path):
    _write_session(tmp_path, "sess-proj", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5, cwd="/home/user/my-app"),
    ])
    stub = StubResolver()
    r = list(ClaudeCliCollector(tmp_path, resolver=stub).collect(date(2026, 7, 3)))[0]
    assert r.project == "RESOLVED"
    assert stub.calls[0] == ("my-app", "my-app")


def test_project_none_without_resolver(tmp_path):
    _write_session(tmp_path, "sess-noproj", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5, cwd="/home/user/work-repo"),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.project is None


def test_end_to_end_with_real_resolver_yes_mode(tmp_path):
    _write_session(tmp_path, "sess-proj", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5, cwd="/home/user/my-app"),
    ])
    resolver = ProjectNameResolver("yes")
    r = list(ClaudeCliCollector(tmp_path, resolver=resolver).collect(date(2026, 7, 3)))[0]
    assert r.project == "my-app"


def test_empty_session_yields_record_with_zero_tokens(tmp_path):
    """A JSONL with no assistant entries still yields a record (zero tokens)."""
    _write_session(tmp_path, "sess-empty", [
        {"type": "last-prompt", "sessionId": "sess-empty"},
    ])
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 1)))
    # No timestamps → included; zero tokens
    assert len(records) == 1
    assert records[0].turns == 0
    assert records[0].input_tokens == 0


def test_malformed_lines_skipped(tmp_path):
    proj_dir = tmp_path / "proj-x"
    proj_dir.mkdir()
    (proj_dir / "sess-bad.jsonl").write_text(
        "NOT JSON\n" + json.dumps(_asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5)),
        encoding="utf-8",
    )
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))
    assert len(records) == 1
    assert records[0].turns == 1


def test_project_uses_repo_slug_when_cwd_is_git_repo(tmp_path):
    repo_dir = tmp_path / "checkout"
    git = repo_dir / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/rbipin/TokenTrace.git\n',
        encoding="utf-8",
    )
    _write_session(tmp_path, "sess-git", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5,
              cwd=str(repo_dir)),
    ])
    stub = StubResolver()
    r = list(ClaudeCliCollector(tmp_path, resolver=stub).collect(date(2026, 7, 3)))[0]
    assert r.project == "RESOLVED"
    assert stub.calls[0] == ("rbipin/TokenTrace", "rbipin/TokenTrace")


def test_tool_calls_counted_from_tool_use_blocks(tmp_path):
    e1 = _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50)
    e1["message"]["content"] = [
        {"type": "text", "text": "let me look"},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
        {"type": "tool_use", "id": "t2", "name": "Grep", "input": {}},
    ]
    e2 = _asst("2026-07-03T10:05:00.000Z", "claude-sonnet-4-6", 100, 20)
    e2["message"]["content"] = [
        {"type": "tool_use", "id": "t3", "name": "Edit", "input": {}},
    ]
    _write_session(tmp_path, "sess-tools", [e1, e2])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.tool_calls == 3


def test_string_content_not_counted_and_no_crash(tmp_path):
    e = _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50)
    e["message"]["content"] = "plain string reply"
    _write_session(tmp_path, "sess-str", [e])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.tool_calls == 0


def test_context_peak_is_max_message_footprint(tmp_path):
    _write_session(tmp_path, "sess-peak", [
        # footprint = 10 + 40000 + 500 + 100 = 40610
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 100,
              cache_create=500, cache_read=40000),
        # footprint = 20 + 44000 + 600 + 90 = 44710  <- peak
        _asst("2026-07-03T10:05:00.000Z", "claude-sonnet-4-6", 20, 90,
              cache_create=600, cache_read=44000),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.context_peak_tokens == 44710


def test_reasoning_tokens_stay_zero(tmp_path):
    _write_session(tmp_path, "sess-r", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.reasoning_tokens == 0
