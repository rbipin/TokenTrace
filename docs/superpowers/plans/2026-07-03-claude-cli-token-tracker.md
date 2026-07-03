# Claude CLI Token Tracker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ClaudeCliCollector` that parses `~/.claude/projects/**/*.jsonl` and stores daily Claude Code token usage (including cache breakdown) into the existing SQLite database.

**Architecture:** A new `ClaudeCliCollector` class implements the existing `ActivityCollector` protocol. It walks JSONL conversation files, extracts token counts from assistant messages, aggregates by `(date, model)`, and yields `ActivityRecord`s. It is wired into `tracker.py` alongside the existing Copilot collectors with no other changes to the pipeline, store, or report layers.

**Tech Stack:** Python 3.12, `pathlib`, `json`, `collections.defaultdict`, `pytest` with `tmp_path` fixture.

## Global Constraints

- Cross-platform: use `Path.home()` for all home-directory references, never hard-code OS-specific separators
- `source` field value for all records from this collector must be exactly `"claude-cli"`
- `scope` field must be `""` (the existing `NO_SCOPE` default)
- `cache_creation_input_tokens` from JSONL maps to `cache_write_tokens` in `ActivityRecord`
- `cache_read_input_tokens` from JSONL maps to `cache_read_tokens` in `ActivityRecord`
- No new Python dependencies — stdlib only
- TDD: write failing tests before implementation code

---

### Task 1: Implement `ClaudeCliCollector` with tests

**Files:**
- Create: `aitoken/collectors/claude_cli.py`
- Create: `tests/test_claude_cli_collector.py`

**Interfaces:**
- Consumes: `ActivityRecord` from `aitoken.models`, `file_touched_since` and `to_local_date` from `aitoken.collectors.base`
- Produces: `ClaudeCliCollector(projects_dir: Path | None = None)` with `.collect(since: date) -> Iterator[ActivityRecord]`

- [ ] **Step 1: Create the test file**

```python
# tests/test_claude_cli_collector.py
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
```

- [ ] **Step 2: Run tests — expect ImportError (collector not yet created)**

```bash
cd /path/to/ai-token
pytest tests/test_claude_cli_collector.py -v
```

Expected: all 6 tests fail with `ImportError: cannot import name 'ClaudeCliCollector'`

- [ ] **Step 3: Create the collector**

```python
# aitoken/collectors/claude_cli.py
"""Collector that reads token usage from Claude Code CLI conversation files."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Iterator

from ..models import ActivityRecord
from .base import file_touched_since, to_local_date

SOURCE = "claude-cli"


class ClaudeCliCollector:
    """Aggregates daily token usage from <projects_dir>/**/*.jsonl."""

    source = SOURCE

    def __init__(self, projects_dir: Path | None = None) -> None:
        self._dir = projects_dir or Path.home() / ".claude" / "projects"

    def collect(self, since: date) -> Iterator[ActivityRecord]:
        if not self._dir.exists():
            return

        totals: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        )

        for jsonl in self._dir.rglob("*.jsonl"):
            if not file_touched_since(jsonl, since):
                continue
            try:
                lines = jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                ts = obj.get("timestamp")
                if not ts:
                    continue
                day = to_local_date(ts)
                if day < since.isoformat():
                    continue
                model = msg.get("model") or "unknown"
                t = totals[(day, model)]
                t["input"] += usage.get("input_tokens", 0)
                t["output"] += usage.get("output_tokens", 0)
                t["cache_write"] += usage.get("cache_creation_input_tokens", 0)
                t["cache_read"] += usage.get("cache_read_input_tokens", 0)

        for (day, model), t in totals.items():
            yield ActivityRecord(
                date=day,
                source=SOURCE,
                model=model,
                input_tokens=t["input"],
                output_tokens=t["output"],
                cache_write_tokens=t["cache_write"],
                cache_read_tokens=t["cache_read"],
            )
```

- [ ] **Step 4: Run tests — expect all 6 to pass**

```bash
pytest tests/test_claude_cli_collector.py -v
```

Expected output:
```
PASSED tests/test_claude_cli_collector.py::test_collects_tokens_from_jsonl
PASSED tests/test_claude_cli_collector.py::test_groups_by_date_and_model
PASSED tests/test_claude_cli_collector.py::test_skips_non_assistant_lines
PASSED tests/test_claude_cli_collector.py::test_since_filter
PASSED tests/test_claude_cli_collector.py::test_missing_directory_yields_nothing
PASSED tests/test_claude_cli_collector.py::test_cache_tokens_mapped_correctly
6 passed
```

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
pytest -v
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add aitoken/collectors/claude_cli.py tests/test_claude_cli_collector.py
git commit -m "feat: add ClaudeCliCollector with cache token tracking"
```

---

### Task 2: Wire `ClaudeCliCollector` into the pipeline

**Files:**
- Modify: `aitoken/config.py:22` (add `claude_projects` field to `Paths`)
- Modify: `aitoken/collectors/__init__.py` (export `ClaudeCliCollector`)
- Modify: `tracker.py:15,26` (import and add to pipeline)

**Interfaces:**
- Consumes: `ClaudeCliCollector` from Task 1
- Produces: `python tracker.py collect` captures both Copilot and Claude CLI rows

- [ ] **Step 1: Add `claude_projects` to `Paths` in `aitoken/config.py`**

Find line 22 (the `copilot_home` field) and add the line directly below it:

```python
    copilot_home: Path = field(default_factory=lambda: Path.home() / ".copilot")
    claude_projects: Path = field(default_factory=lambda: Path.home() / ".claude" / "projects")
```

- [ ] **Step 2: Export `ClaudeCliCollector` from `aitoken/collectors/__init__.py`**

Replace the current content of `aitoken/collectors/__init__.py` with:

```python
"""Collectors package."""

from .base import ActivityCollector
from .claude_cli import ClaudeCliCollector
from .copilot_cli import CopilotCliCollector

__all__ = [
    "ActivityCollector",
    "ClaudeCliCollector",
    "CopilotCliCollector",
]
```

- [ ] **Step 3: Wire `ClaudeCliCollector` into `tracker.py`**

Change line 15 from:
```python
from aitoken.collectors import CopilotCliCollector
```
to:
```python
from aitoken.collectors import ClaudeCliCollector, CopilotCliCollector
```

Change lines 23–29 (`_build_pipeline` body) from:
```python
    paths = cfg.paths
    return (
        TrackerPipeline()
        .add(CopilotCliCollector(paths.copilot_home))
        .since(since)
        .store(UsageStore(cfg.db_path))
    )
```
to:
```python
    paths = cfg.paths
    return (
        TrackerPipeline()
        .add(CopilotCliCollector(paths.copilot_home))
        .add(ClaudeCliCollector(paths.claude_projects))
        .since(since)
        .store(UsageStore(cfg.db_path))
    )
```

- [ ] **Step 4: Run the full test suite**

```bash
pytest -v
```

Expected: all tests pass (including Task 1's 6 tests).

- [ ] **Step 5: Smoke-test against real Claude CLI data**

```bash
python tracker.py collect --lookback 7
python tracker.py report --period day
```

Expected: output includes `claude-cli` rows alongside any Copilot rows, with non-zero token counts and cache columns. No error about missing `~/.claude/projects/`.

- [ ] **Step 6: Commit**

```bash
git add aitoken/config.py aitoken/collectors/__init__.py tracker.py
git commit -m "feat: wire ClaudeCliCollector into tracker pipeline"
```
