# Design: TokenTracer Architecture Documentation

**Date:** 2026-07-07
**Status:** Approved

## Goal

Produce `docs/ARCHITECTURE.md` — a contributor-facing architecture document
describing how TokenTracer works end to end, with emphasis on the collect
data flow and the exact source files each collector reads (Copilot CLI and
Claude CLI). Linked from the README.

## Audience

Developers/contributors. Include file paths, protocols, schema details, and
extension points.

## Deliverable

A single new file `docs/ARCHITECTURE.md` plus a one-line link from
`README.md`. Diagrams in Mermaid (renders on GitHub). No code changes.

## Document outline

1. **Overview** — one paragraph + Mermaid component diagram
   (CLI → pipeline → collectors → stores → report).
2. **Collect flow** — Mermaid sequence diagram of `tracker.py collect`:
   `_build_pipeline()` → `TrackerPipeline` (parallel collection via
   `ThreadPoolExecutor`) → `merge_records` dedupe on
   `(session_id, source, model)` → context stamping →
   `SqliteStore.upsert` (last-write-wins, idempotent).
3. **Data sources** — per collector: exact files read, schema variants,
   parsing rules, a "fields read" table, and a small Mermaid flowchart.
   - **Copilot CLI** (`src/collectors/copilot_cli.py`):
     - `~/.copilot/session-store.db` → `sessions` table (id, cwd,
       repository, timestamps). Supports old (`startedAt`/`endedAt`) and
       new (`created_at`/`updated_at`) schemas via `PRAGMA table_info`.
     - `~/.copilot/session-state/<session-id>/events.jsonl` per session:
       completed sessions use the `session.shutdown` event's `modelMetrics`
       (old format: flat counts + `turns`; new format: counts under `usage`,
       turns under `requests.count`) — one record per `(session, model)`;
       active sessions fall back to summing `assistant.message` usage.
       New CLI nests event payloads under a `data` key.
   - **Claude CLI** (`src/collectors/claude_cli.py`):
     - `~/.claude/projects/**/*.jsonl` — one conversation per file, file
       stem = session id. mtime pre-filter skips files older than the
       lookback window. Sums `message.usage` fields (`input_tokens`,
       `output_tokens`, `cache_creation_input_tokens`,
       `cache_read_input_tokens`) from `type: "assistant"` entries;
       session start/end from min/max `timestamp`; project from `cwd`
       when `--track-projects` is on.
   - Note the read-only invariant and why no VS Code/Web/Desktop
     collectors exist (those surfaces never persist token data).
4. **Storage** — `sessions` table schema, PRIMARY KEY
   `(session_id, source, model)`, sync-tracking, legacy-table drop warning.
5. **Report flow** — periods (all/day/month/year), default detailed view,
   `--summary`, `--by-project`, cache-hit calculation.
6. **Configuration** — `~/.tokentracer.toml` keys, `~/.tokentracer.env`,
   `${VAR}` expansion order (environ first, then env file).
7. **Sync & stores registry** — `SessionStore` protocol, entry-point
   discovery with built-in fallback, per-store unsynced tracking,
   Supabase store, Mermaid diagram of the sync flow.
8. **Extending** — checklists for adding a new collector and a new store
   (mirroring CLAUDE.md).

## Validation

Docs-only change: verify Mermaid blocks are syntactically valid and the doc
renders; no tests or builds required. Cross-check every stated file path,
key name, and schema detail against the source code before finalizing.

## Out of scope

- Any refactoring or code changes.
- End-user tutorial content (README already covers usage).
