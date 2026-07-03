# Claude CLI Token Tracker — Design Spec

**Date:** 2026-07-03
**Status:** Approved

## Goal

Add daily Claude Code CLI token tracking to the existing AI token tracker, with accurate cache token breakdown (cache read / cache write), stored in the same SQLite database alongside Copilot usage.

## Scope

- **In scope:** Claude Code CLI only (`~/.claude/projects/` JSONL files), cross-platform (Mac + Windows)
- **Out of scope:** claude.ai web app, Claude desktop app, VS Code Claude extension, Anthropic API calls from user code

## Data Source

Claude Code CLI stores every conversation as a JSONL file under `~/.claude/projects/<project-id>/<conversation-id>.jsonl`. Each line is a JSON object. Assistant messages have the shape:

```json
{
  "type": "assistant",
  "timestamp": "2026-07-03T03:38:34.435Z",
  "message": {
    "model": "claude-sonnet-4-6",
    "usage": {
      "input_tokens": 3,
      "output_tokens": 183,
      "cache_creation_input_tokens": 61506,
      "cache_read_input_tokens": 0
    }
  }
}
```

The path `~/.claude/projects/` resolves identically on Mac and Windows via `Path.home()`.

## Data Model

No schema changes required. `ActivityRecord` already has `cache_read_tokens` and `cache_write_tokens` fields and handles them in `merge()`.

Field mapping:

| JSONL field | ActivityRecord field |
|---|---|
| `message.usage.input_tokens` | `input_tokens` |
| `message.usage.output_tokens` | `output_tokens` |
| `message.usage.cache_creation_input_tokens` | `cache_write_tokens` |
| `message.usage.cache_read_input_tokens` | `cache_read_tokens` |
| `message.model` | `model` |
| `timestamp` (date part) | `date` |
| `"claude-cli"` (constant) | `source` |
| `""` (constant) | `scope` |

`context_peak_tokens` and `reasoning_tokens` are left at their default `0` (not available in Claude's usage response).

**Storage grain:** `(date, source, model, scope)` — one record per day per model, aggregated across all projects.

## New Collector: `ClaudeCliCollector`

**File:** `aitoken/collectors/claude_cli.py`

Implements the existing `ActivityCollector` protocol.

**Algorithm:**

1. Resolve `~/.claude/projects/` via `Path.home()`. If the directory does not exist, yield nothing (graceful on machines without Claude Code).
2. Recursively walk all `*.jsonl` files. Apply a fast-path filter: skip files whose `mtime` is before the `since` date.
3. For each file, parse each line as JSON. Skip lines where `type != "assistant"` or `message.usage` is absent.
4. Extract `(date, model, input, output, cache_write, cache_read)` from each matching line.
5. Skip records whose date is before `since`.
6. Group by `(date, model)`, summing all four token fields.
7. Return one `ActivityRecord` per group.

## Pipeline Wiring

`tracker.py` adds `ClaudeCliCollector` alongside existing Copilot collectors. No flags or config needed — the collector auto-skips if `~/.claude/projects/` is absent, making it safe on machines without Claude Code installed.

## Report Output

Claude CLI rows appear as a new source group in the existing report. Cache token fields are shown for `claude-cli` rows since they carry billing weight:

```
claude-cli  claude-sonnet-4-6   2026-07-03
  input: 1,234  output: 567  cache_read: 89,012  cache_write: 3,456
```

Copilot rows are unchanged (their cache fields stay 0).

## Tests

**File:** `tests/test_claude_cli_collector.py`

| Test | What it verifies |
|---|---|
| `test_collects_tokens_from_jsonl` | Two assistant messages on same date/model merge into one record with summed tokens |
| `test_groups_by_date_and_model` | Messages on different dates or models produce separate records |
| `test_skips_non_assistant_lines` | User messages, summaries, system lines are ignored |
| `test_since_filter` | Messages before the `since` date are excluded |
| `test_missing_directory_yields_nothing` | Non-existent path returns empty list without raising |
| `test_cache_tokens_mapped_correctly` | `cache_creation_input_tokens` → `cache_write_tokens`, `cache_read_input_tokens` → `cache_read_tokens` |

Tests use `tmp_path` (pytest) to create fake project directories and JSONL files — no dependency on real `~/.claude/` data.

## What Is Not Changing

- `ActivityRecord` schema — no fields added or removed
- SQLite schema — no migration needed
- Existing Copilot collectors — untouched
- Scheduling setup — existing Task Scheduler / launchd config runs `tracker.py` daily unchanged
