# Copilot Usage Tracker — Design

**Date:** 2026-06-15
**Status:** Approved (brainstorming complete)

## Goal

A Python tool that runs periodically on a Windows machine to track local GitHub
Copilot **activity** across editors, stored at a fine daily grain so it can be
aggregated later **per day, per month, per year, and per model**.

## Scope decisions (and why)

These were settled during brainstorming after investigating what is actually
persisted on disk:

| Decision | Outcome | Reason |
|---|---|---|
| Token counts (prompt/completion) | **Out of scope** | Not written to disk by VS Code Copilot Chat or the CLI; only shown live in the Chat Debug View (in memory). |
| Peak tokens per request | **Out of scope** | Same reason — no per-request token data persisted. |
| GitHub billing API (AI credits) | **Out of scope** | Copilot is org-managed; user has no org-admin/billing access, so the org-level usage endpoint is unavailable and the user-level endpoint excludes org-billed usage. |
| Primary metric | **Activity metrics + CLI context-window peaks**, fully local, periodic | Only reliable, accurate, locally available signal. |
| Storage grain | **One row per day × source × model × scope** | Enables month/year/model rollups as pure read-time `GROUP BY`; never pre-aggregate destructively. |

## Verified data sources

### 1. VS Code Copilot Chat
- Path: `%APPDATA%\Code\User\workspaceStorage\<wsId>\chatSessions\*.json`
  (plus `Code - Insiders`).
- Per session file: `sessionId`, `creationDate`, `lastMessageDate`,
  `requests[]` where each request has `modelId`, `timestamp`, `message`,
  `result.timings`, `agent`.
- Derived daily metrics: prompts (request count), sessions, model mix,
  per-workspace attribution, first/last activity.
- Note: `prompt_tokens` only appears in `models.json` as model *limits*, not
  usage. `debug-logs\main.jsonl` only contains `session_start` spans.

### 2. Copilot CLI
- `%USERPROFILE%\.copilot\session-store.db` (SQLite):
  - `sessions(id, cwd, repository, host_type, branch, summary, created_at, updated_at)`
  - `turns(id, session_id, turn_index, user_message, assistant_response, timestamp)`
  - Clean source for sessions/day, turns/day, repos, branches.
- `%USERPROFILE%\.copilot\logs\process-*.log`:
  - `CompactionProcessor: Utilization 47.6% (95163/200000 tokens)` →
    **context-window peak** (max used tokens against window).
  - Compaction events and `Session named: "..."`.
- `%USERPROFILE%\.copilot\session-state\<sid>\events.jsonl` (optional):
  model per turn from `assistant.message` events.

### 3. Visual Studio 2022 (17.0) / 2026 (18.0)
- Path: `%LOCALAPPDATA%\Microsoft\VisualStudio\<ver>\VSGitHubCopilot\copilot-chat\`.
- Currently empty/minimal on this machine. Collector ships but **no-ops
  gracefully** until VS Copilot chat data appears. Best-effort / extensible.

## Architecture (SOLID, collector-per-surface)

Strategy + Open/Closed: a common collector interface; one collector per surface;
add new surfaces without touching existing ones. Fluent pipeline for wiring.

```
ActivityCollector (Protocol)   -> collect(since) -> Iterable[ActivityRecord]
  ├─ VSCodeChatCollector
  ├─ CopilotCliCollector        (session-store.db + process logs)
  └─ VisualStudioCollector      (best-effort)

ActivityRecord (frozen dataclass, normalized)
  date, source, model, scope,
  sessions, prompts, turns, tool_calls,
  context_peak_tokens, first_ts, last_ts

UsageStore (SQLite sink)        -> idempotent upsert at daily grain
UsageReporter                   -> day / month / year / model rollups (GROUP BY)
TrackerPipeline (fluent)        -> .add(collector).since(date).store(store).run()
```

### Storage schema (finest grain)

```sql
CREATE TABLE daily_activity (
  date                TEXT NOT NULL,   -- YYYY-MM-DD (local)
  source              TEXT NOT NULL,   -- vscode | vscode-insiders | copilot-cli | visual-studio
  model               TEXT NOT NULL,   -- e.g. claude-sonnet-4 ; 'unknown' if absent
  scope               TEXT NOT NULL,   -- workspace folder or repo; '' if n/a
  sessions            INTEGER NOT NULL DEFAULT 0,
  prompts             INTEGER NOT NULL DEFAULT 0,
  turns               INTEGER NOT NULL DEFAULT 0,
  tool_calls          INTEGER NOT NULL DEFAULT 0,
  context_peak_tokens INTEGER NOT NULL DEFAULT 0,
  first_ts            TEXT,
  last_ts             TEXT,
  updated_at          TEXT NOT NULL,
  PRIMARY KEY (date, source, model, scope)
);
```

- **Idempotent:** each run recomputes the lookback window (default: today + N
  prior days) and `UPSERT`s. Chat files/DB are cumulative, so re-scanning is safe.
- **Rollups (read time):**
  - Monthly: `GROUP BY strftime('%Y-%m', date), model`
  - Yearly:  `GROUP BY strftime('%Y', date), model`
  - Counts use `SUM(...)`; context peak uses `MAX(context_peak_tokens)`.

## Run flow

1. Resolve config (paths, lookback days, db location).
2. Build pipeline fluently and run:
   ```python
   (TrackerPipeline()
       .add(VSCodeChatCollector())
       .add(CopilotCliCollector())
       .add(VisualStudioCollector())
       .since(today - lookback)
       .store(UsageStore(db_path))
       .run())
   ```
3. Each collector scans only files modified within the lookback window and emits
   `ActivityRecord`s.
4. Store upserts at daily grain; log a one-line summary.

## CLI

- `tracker.py collect` — scheduled job (default).
- `tracker.py report --period day|month|year [--source ...] [--model ...]` — table.
- `tracker.py report ... --json` — machine-readable for a future dashboard.

## Project layout

```
ai-token/
├─ tracker.py                # CLI entry (argparse)
├─ aitoken/
│  ├─ models.py              # ActivityRecord (frozen dataclass)
│  ├─ collectors/
│  │  ├─ base.py             # ActivityCollector protocol + helpers
│  │  ├─ vscode.py
│  │  ├─ copilot_cli.py
│  │  └─ visual_studio.py
│  ├─ store.py               # UsageStore (sqlite upsert)
│  ├─ report.py              # UsageReporter (rollups)
│  ├─ pipeline.py            # fluent TrackerPipeline
│  └─ config.py              # paths, lookback, db location
├─ tests/                    # pytest with sample fixtures per collector
├─ requirements.txt          # stdlib-only target
└─ README.md                 # setup + Task Scheduler instructions
```

## Non-functional

- **Stdlib-only** (`sqlite3`, `json`, `pathlib`, `argparse`, `re`). No external deps.
- **Read-only** access to editor data; the tool only writes its own SQLite db.
- Each collector independently testable with fixture files.
- VS collector ships but no-ops gracefully until data appears.

## Future extensions (not now)

- Optional token capture via local HTTPS proxy (accurate, continuous).
- Optional token estimation via re-tokenizing stored text.
- Dashboard / charts over the JSON report output.
