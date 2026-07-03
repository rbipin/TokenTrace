"""Tests for ClaudeCliCollector."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from aitoken.collectors.claude_cli import ClaudeCliCollector


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(m) for m in messages), encoding="utf-8")


def _assistant(
    ts: str,
    model: str,
    input_t: int,
    output_t: int,
    cache_write: int = 0,
    cache_read: int = 0,
) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cache_creation_input_tokens": cache_write,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def test_collects_tokens_from_jsonl(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "proj1" / "conv.jsonl",
        [
            _assistant("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50),
            _assistant("2026-07-03T11:00:00.000Z", "claude-sonnet-4-6", 200, 75),
        ],
    )
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 1)))
    assert len(records) == 1
    assert records[0].input_tokens == 300
    assert records[0].output_tokens == 125
    assert records[0].source == "claude-cli"


def test_groups_by_date_and_model(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "proj1" / "conv.jsonl",
        [
            _assistant("2026-07-01T12:00:00.000Z", "claude-sonnet-4-6", 100, 50),
            _assistant("2026-07-02T12:00:00.000Z", "claude-sonnet-4-6", 200, 75),
            _assistant("2026-07-02T12:30:00.000Z", "claude-opus-4-8", 300, 100),
        ],
    )
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 6, 28)))
    assert len(records) == 3
    [opus_rec] = [r for r in records if r.model == "claude-opus-4-8"]
    assert opus_rec.input_tokens == 300
    sonnet_recs = sorted(
        [r for r in records if r.model == "claude-sonnet-4-6"],
        key=lambda r: r.input_tokens,
    )
    assert sonnet_recs[0].input_tokens == 100
    assert sonnet_recs[1].input_tokens == 200


def test_skips_non_assistant_lines(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "proj1" / "conv.jsonl",
        [
            {"type": "user", "timestamp": "2026-07-03T10:00:00.000Z", "content": "hello"},
            {"type": "system", "timestamp": "2026-07-03T10:00:00.000Z"},
            {"type": "summary", "timestamp": "2026-07-03T10:00:00.000Z"},
            _assistant("2026-07-03T11:00:00.000Z", "claude-sonnet-4-6", 50, 25),
        ],
    )
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 1)))
    assert len(records) == 1
    assert records[0].input_tokens == 50


def test_since_filter(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "proj1" / "conv.jsonl",
        [
            _assistant("2026-06-30T12:00:00.000Z", "claude-sonnet-4-6", 999, 999),
            _assistant("2026-07-02T12:00:00.000Z", "claude-sonnet-4-6", 100, 50),
        ],
    )
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 2)))
    assert len(records) == 1
    assert records[0].input_tokens == 100


def test_missing_directory_yields_nothing(tmp_path: Path) -> None:
    records = list(ClaudeCliCollector(tmp_path / "nonexistent").collect(date(2026, 7, 1)))
    assert records == []


def test_cache_tokens_mapped_correctly(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "proj1" / "conv.jsonl",
        [
            _assistant(
                "2026-07-03T10:00:00.000Z",
                "claude-sonnet-4-6",
                10,
                20,
                cache_write=500,
                cache_read=1000,
            ),
        ],
    )
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 1)))
    assert len(records) == 1
    assert records[0].cache_write_tokens == 500
    assert records[0].cache_read_tokens == 1000
