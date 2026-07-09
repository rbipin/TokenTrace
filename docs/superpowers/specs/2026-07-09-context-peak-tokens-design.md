# Context Peak Tokens — Design

**Date**: 2026-07-09
**Status**: Approved

## Problem

`SessionRecord.context_peak_tokens` exists in the model and the `sessions` table but is
never populated — every row stores the default `0`. The per-request data needed to compute
it lives only in the source files (Copilot's `session-store.db`, Claude's JSONLs), which
are eventually pruned. Our database stores per-session **sums**, and a max cannot be
recovered from a sum — so if the peak isn't captured at collect time, it is lost.

## Semantics

`context_peak_tokens` = the largest total token footprint of any single API request in the
session:

```
peak = MAX over requests of (prompt tokens + output tokens)
     where prompt tokens = input + cache read + cache write
```

- Values come from API-reported usage numbers — exact, not estimated.
- Computed per `(session_id, source, model)`, matching the record merge key.
- Subagent requests (which run in their own context windows) are **excluded**; the peak
  reflects the main conversation only.
- Defaults to `0` when the data is unavailable (older Copilot CLI installs without the
  usage table, sessions with no assistant activity). No fallback estimation.

## Data sources

### Copilot CLI

`events.jsonl` does **not** carry per-message input/cache tokens (`assistant.message`
events persist only `outputTokens`), so the peak cannot come from the file the collector
parses today. Instead, Copilot CLI writes an `assistant_usage_events` table in
`~/.copilot/session-store.db` with one row per API request:

| Column | Meaning |
|---|---|
| `session_id` | session the request belongs to |
| `turn_index` | conversation turn |
| `agent_id` | `NULL` for the main conversation; set for subagent requests |
| `model` | model id for the request |
| `input_tokens` | total prompt tokens — **already includes** cache read + cache write |
| `output_tokens` | response tokens |
| `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` | breakdown fields |

Because `input_tokens` is cache-inclusive, the per-request footprint is simply
`input_tokens + output_tokens`.

### Claude CLI

Each assistant message line in `~/.claude/projects/**/*.jsonl` carries exact usage:

```json
"usage": {"input_tokens": 12, "cache_read_input_tokens": 44076,
          "cache_creation_input_tokens": 601, "output_tokens": 98}
```

Here `input_tokens` **excludes** cache, so the per-message footprint is
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens + output_tokens`.
Claude sessions have no subagent rows in these files, so no filtering is needed.

## Design

### Copilot collector (`src/collectors/copilot_cli.py`)

While `session-store.db` is already open in `collect()` (before the connection closes and
event parsing begins), run one bulk query:

```sql
SELECT session_id, model, MAX(input_tokens + output_tokens) AS peak
FROM assistant_usage_events
WHERE agent_id IS NULL
GROUP BY session_id, model
```

Load the result into `dict[(session_id, model)] -> peak`. If the table does not exist
(`sqlite3.OperationalError`), use an empty dict — peaks stay `0`, no warning storm.
Pass the dict into `_parse_events` / `_from_shutdown` and set
`context_peak_tokens=peaks.get((session_id, model), 0)` on each yielded record.

Rationale for bulk-preload over per-session queries: the connection is opened exactly
once and closed before per-session event parsing; per-session queries would require
holding or reopening it, for no correctness gain.

### Claude collector (`src/collectors/claude_cli.py`)

In `_parse_session`'s existing assistant-message loop, compute the per-message footprint
and track a running max; set it on the returned `SessionRecord`. No new I/O.

### Storage

Local: no changes — `SessionRecord.context_peak_tokens`, the `sessions` column, and the
SQLite upsert already exist. Remote: add `context_peak_tokens` to the Supabase upsert
payload in `src/stores/supabase.py` (the remote `token_sessions` table needs a matching
column). Upsert remains last-write-wins,
so re-collecting with `--lookback N` backfills peaks for historical sessions whose source
files still exist.

### Reporting (`src/report.py`)

Add a `CtxPeak` column to the **default detailed session view**, formatted like the other
token columns. Summary and aggregated period views are unchanged for now.

### Documentation

- `docs/ARCHITECTURE.md`: document the semantics, the `assistant_usage_events` table
  (schema and cache-inclusive `input_tokens`), the Claude per-message computation, and
  the subagent exclusion.
- `CLAUDE.md`: brief mentions under the Copilot and Claude data-details sections.

## Testing

Extend existing test files (fixtures via `tmp_path`, hermetic):

- **Claude**: fixture JSONL with multiple assistant messages of varying usage; assert the
  record carries the correct max (including cache components).
- **Copilot**: fixture `session-store.db` with an `assistant_usage_events` table —
  assert correct per-(session, model) peak; include subagent rows (`agent_id` set) to
  prove exclusion; include a db **without** the table to prove peaks default to `0`
  without errors.
- **Report**: assert the `CtxPeak` column renders in the detailed view.

## Out of scope

- Estimating peaks from `events.jsonl` output tokens on old Copilot installs.
- Surfacing peaks in summary/aggregate report views.
- Backfill tooling beyond the existing idempotent `collect --lookback`.
