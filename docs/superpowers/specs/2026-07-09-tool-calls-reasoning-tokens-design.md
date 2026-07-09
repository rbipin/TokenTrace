# Tool Calls Capture & Reasoning Tokens Surfacing — Design

**Date:** 2026-07-09
**Status:** Approved

## Problem

- `SessionRecord.tool_calls` exists (with a `sessions.tool_calls` column) but neither collector populates it — every stored row is 0.
- `reasoning_tokens` **is** correctly collected for Copilot CLI (verified: 165/241 rows > 0 in the live DB), but `UsageReporter` never displays it, so it appears to be zero.
- Claude CLI exposes no reasoning-token field in its JSONL `usage` (thinking tokens are folded into `output_tokens`), so Claude reasoning stays 0 by design.

- `SupabaseStore.upsert` omits `tool_calls`, `reasoning_tokens`, and `context_peak_tokens` from pushed rows — remote data silently loses those fields.
- There is no way to dump every DB row with every column for inspection.

## Goals

1. Collect per-session tool-call counts from both collectors.
2. Surface `Reasoning` and `Tools` columns in the detailed report view.
3. Add a `report --detailed` flag: all rows in the DB (no period filter), all columns, including sync status.
4. Push `tool_calls`, `reasoning_tokens`, and `context_peak_tokens` to remote stores (Supabase).

Non-goals: per-tool-name breakdowns, schema/sync changes, backfill tooling (idempotent re-collect naturally backfills sessions still on disk), Claude reasoning estimation.

## Design

### Copilot CLI collector (`src/collectors/copilot_cli.py`)

`_parse_events` keeps its single pass but adds a `tool_calls_by_model: dict[str, int]`:

- For each `tool.execution_complete` event, increment the count for `payload.get("model")` (fallback: the session's currently-detected model, else `UNKNOWN_MODEL`).
- The shutdown event is the last event in the file, but the early `return` on `session.shutdown` is removed: remember the shutdown payload, finish the scan, then emit. This lets tool counts (which only exist as discrete events, not in `modelMetrics`) be attached to shutdown-derived records.
- `_from_shutdown` receives the counts dict and sets `tool_calls=counts.get(model, 0)` per record. (Verified: shutdown `modelMetrics` contains `requests.count` but no tool-call counts, so event counting is the only source.)
- Fallback path (no shutdown event): `tool_calls=sum(counts.values())`, attributed to the single detected model.
- `reasoning_tokens` handling is unchanged — it already reads `usage.reasoningTokens` in both paths.

### Claude CLI collector (`src/collectors/claude_cli.py`)

In the assistant-message branch, count `tool_use` content blocks:

```python
content = msg.get("content") or []
if isinstance(content, list):
    tool_calls += sum(
        1 for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    )
```

String-content messages are skipped safely. `tool_calls` is set on the returned `SessionRecord`. `reasoning_tokens` remains 0 (no source data).

### Reporting (`src/report.py`)

Detailed session view gains two columns: `Reasoning` (after `Output`) and `Tools` (after `Turns`). Summary and `--by-project` views are unchanged (kept compact). Verify `--json` output includes both fields.

### `report --detailed` flag (`src/commands/report.py`, `src/report.py`)

New boolean flag on the `report` command implemented as a new `ReportStrategy` (follows the existing strategy pattern; `_pick_strategy` routes to it when `detailed=True`, taking precedence over `--summary`/`--by-project`):

- **All rows** — ignores `--period` (implicit `all`); `--model` filter still applies.
- **All columns** — every `sessions` column: SessionId, Source, Model, Date, Start, End, Project, Turns, Tools, Input, Output, CacheCreate, CacheRead, ContextPeak, Reasoning, Context, plus **Synced** — comma-separated store names from `sync_log` for that row (empty if never synced), via a LEFT JOIN / GROUP_CONCAT.
- `--json` works with it, emitting one object per row with all fields.

### Remote store completeness (`src/stores/supabase.py`)

`SupabaseStore.upsert` row dict gains `tool_calls`, `reasoning_tokens`, and `context_peak_tokens` (currently omitted). Users must add these columns to their Supabase `token_sessions` table; note this in README. Conflict key unchanged.

### Invariants preserved

- Collectors stay read-only; collect stays idempotent (last-write-wins upsert on `(session_id, source, model)`).
- No schema changes: `tool_calls` and `reasoning_tokens` columns already exist in `SqliteStore` and are already pushed by remote stores.

## Testing

- **Copilot:** fixture events.jsonl with `tool.execution_complete` events + shutdown → per-model tool counts on shutdown records; fixture without shutdown → summed fallback count; multi-model fixture → correct attribution by event `model` field.
- **Claude:** fixture JSONL with `tool_use` blocks → correct count; entry with string content → no crash, not counted.
- **Report:** detailed view renders `Reasoning` and `Tools` columns; `--json` includes the fields.
- **`--detailed`:** returns every row regardless of date; includes all columns; Synced column reflects `sync_log` entries; works with `--json` and `--model`.
- **Supabase:** upsert payload includes `tool_calls`, `reasoning_tokens`, `context_peak_tokens` (assert on mocked client).

## Backfill

None needed. Re-running `tracker.py collect --lookback N` overwrites rows for sessions whose source files still exist.
