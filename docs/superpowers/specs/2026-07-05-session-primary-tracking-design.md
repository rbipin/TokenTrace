# Session-Primary Tracking Design

**Date:** 2026-07-05  
**Branch:** additional-data  
**Status:** Approved

## Overview

Refactor the tracker from a day-grain store to a session-grain store. Each collected record represents one conversation session (one Claude Code JSONL file, one Copilot session). Day/month aggregates are derived at query time via `GROUP BY`. Add opt-in project name tracking, cache efficiency reporting, and two new report views: `--by-project` and `--sessions`.

## Motivation

- The current `(date, source, model)` merge key discards session identity, making it impossible to tie a token spike to a specific work session.
- Cache-read share is the most meaningful efficiency metric and is not surfaced in reports today.
- Project-level breakdown requires knowing which session belonged to which repo.

## Data Model

`ActivityRecord` is renamed to `SessionRecord` (frozen dataclass). New and changed fields:

| Field | Type | Notes |
|---|---|---|
| `session_id` | `str` | JSONL filename UUID / Copilot session ID |
| `source` | `str` | `"claude_cli"` / `"copilot_cli"` |
| `model` | `str` | First model seen in session |
| `date` | `date` | Derived from `start_ts` |
| `start_ts` | `datetime \| None` | First timestamped entry |
| `end_ts` | `datetime \| None` | Last timestamped entry |
| `project` | `str \| None` | `Path(cwd).name`; `None` when tracking disabled |
| `turns` | `int` | Count of assistant messages |
| `input_tokens` | `int` | Sum across session |
| `output_tokens` | `int` | Sum across session |
| `cache_creation_tokens` | `int` | Sum across session |
| `cache_read_tokens` | `int` | Sum across session |
| `context_peak_tokens` | `int` | Max context window usage (Copilot) |

Removed: `scope`, `prompts` (redundant with `turns`).

Merge key: `(session_id, source)`. Upsert replaces the entire row — re-collecting a session is idempotent because the source data is immutable.

`merge_records` in `models.py` is updated to use the new merge key. On collision, the incoming record wins (last-write-wins is safe since source data does not change).

## Configuration

**File:** `~/.tokentracer.toml`

```toml
[tracking]
track_project_names = true   # default: false
```

Loaded via `tomllib` (Python 3.11+ stdlib). The file is optional; missing keys fall back to defaults.

**CLI override** on the `collect` subcommand:

```
--track-projects        # force on for this run
--no-track-projects     # force off for this run
```

**Write helper:**

```
python3 tracker.py config set track_project_names true
```

This writes (or updates) `~/.tokentracer.toml`. Implemented with a simple string serialiser — no external TOML library needed for writing.

**Precedence:** CLI flag > toml file > default (`False`).

When `track_project_names=False`, `project` is set to `None` on every `SessionRecord` before the DB write. No working directory information is ever stored.

## Storage

The `usage` table is replaced by `sessions`:

```sql
CREATE TABLE sessions (
    session_id             TEXT NOT NULL,
    source                 TEXT NOT NULL,
    model                  TEXT NOT NULL,
    date                   TEXT NOT NULL,
    start_ts               TEXT,
    end_ts                 TEXT,
    project                TEXT,
    turns                  INTEGER DEFAULT 0,
    input_tokens           INTEGER DEFAULT 0,
    output_tokens          INTEGER DEFAULT 0,
    cache_creation_tokens  INTEGER DEFAULT 0,
    cache_read_tokens      INTEGER DEFAULT 0,
    context_peak_tokens    INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, source)
)
```

The existing `ALTER TABLE` migration pattern in `store.py` is replaced by a `CREATE TABLE IF NOT EXISTS` for the new table. On first run with the new schema, `usage` is dropped (after logging a warning that a `--lookback` re-collect is needed to restore history).

**Re-backfill:** `python3 tracker.py collect --lookback 90` reads all JSONL files from the last 90 days and repopulates `sessions`. No data migration code is needed because `collect` is idempotent.

## Collectors

### `ActivityCollector` protocol

Return type annotation changes from `Iterable[ActivityRecord]` to `Iterable[SessionRecord]`. Signature is otherwise unchanged.

### `ClaudeCliCollector`

One `SessionRecord` per JSONL file under `~/.claude/projects/**/*.jsonl`:

- `session_id`: filename stem (UUID)
- `model`: from first `message.model` field on an assistant entry
- `start_ts` / `end_ts`: min/max `timestamp` fields across all entries
- `turns`: count of `type == "assistant"` entries
- Token fields: sum across all `message.usage` blocks on assistant entries (`input_tokens`, `output_tokens`, `cache_creation_input_tokens` → `cache_creation_tokens`, `cache_read_input_tokens` → `cache_read_tokens`)
- `project`: `Path(entry["cwd"]).name` from the first entry that has a `cwd` field; `None` if `track_project_names=False` or no `cwd` found

Files whose `start_ts` is before `since` are skipped.

### `CopilotCliCollector`

The existing `scope` field (Copilot session ID) maps to `session_id`. Session start/end timestamps are extracted from existing event log parsing. `project` is derived from the Copilot session's working directory in the same way as Claude CLI.

## Reporting

### Cache efficiency header

Shown on all report output (text and JSON):

```
Cache efficiency: 76% read from cache (~90% cost saved)
```

Computed as `cache_read_tokens / (input_tokens + cache_read_tokens + cache_creation_tokens)`. The "~90% cost saved" annotation is derived from the approximate 10× price ratio of cache-read vs full input tokens for Claude models.

### Default report (unchanged behaviour)

```sql
SELECT date, source, model,
    SUM(turns), SUM(input_tokens), SUM(output_tokens),
    SUM(cache_creation_tokens), SUM(cache_read_tokens)
FROM sessions
GROUP BY date, source, model
```

### `--by-project`

Groups by `(date, project)`. Rows where `project IS NULL` are excluded. If all projects are null, the report prints a note: *"Project tracking is disabled — run with `--track-projects` or set `track_project_names = true` in `~/.tokentracer.toml`."*

### `--sessions`

Raw session rows ordered by `start_ts DESC`. Columns: session_id (first 8 chars), project (or `—`), date, start→end time, turns, total tokens, cache-hit %. Filters (`--period`, `--model`) apply as today.

Both new flags compose with `--period` and `--model`.

## Error handling

- Missing or malformed JSONL entries are skipped silently (existing behaviour).
- A session file with no assistant entries yields a `SessionRecord` with zero tokens and zero turns — it is stored but has no reporting impact.
- If `~/.tokentracer.toml` exists but is invalid TOML, the tracker prints a warning and falls back to defaults.

## Testing

- `SessionRecord` unit tests in `tests/test_models.py`: merge key, upsert collision behaviour.
- `ClaudeCliCollector` tests in `tests/test_claude_cli_collector.py`: existing fixtures extended with `cwd`, `sessionId`, `timestamp` fields; assert `project` field populated when tracking enabled, `None` when disabled.
- `UsageStore` tests in `tests/test_store_report.py`: insert sessions, verify day-grain GROUP BY queries, verify `--by-project` and `--sessions` query results.
- Config loading tests: missing file → defaults; valid toml → values loaded; invalid toml → warning + defaults; CLI flag overrides toml.
