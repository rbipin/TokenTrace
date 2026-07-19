# Local Usage Dashboard

## Problem

Viewing token usage today requires the `tokentracer report` CLI — text tables in a terminal, no charts, no at-a-glance trends. Goal: a lightweight local web dashboard that visualizes the same `usage.db` data (totals, trends, per-project/per-model/per-source breakdowns) without adding any new data collection — it's a read-only view over what `collect` already writes.

## Architecture

`tokentracer dashboard` starts a stdlib-only `http.server` process that serves a small JSON API plus a built static frontend (React + Vite, built ahead of time). No new runtime dependency — matches `CLAUDE.md`'s stdlib-only constraint. The frontend is manual-refresh only (a "Refresh" button re-fetches); there is no polling.

```
tokentracer dashboard              # foreground: prints URL, blocks until Ctrl-C, no auto-open browser
tokentracer dashboard --daemon     # installs a persistent background service (survives reboot/logout)
tokentracer dashboard --stop       # removes the persistent service
tokentracer dashboard --port 8420  # override default port (default: 8420)
```

Binds to `127.0.0.1` only — no auth, since this is a personal local tool never exposed to a network interface. The port is fixed (not OS-assigned) because the daemon is meant to be a stable, bookmarkable long-running service, not a one-off ephemeral process.

## Command modes

- **`DashboardCommand`** (`src/commands/dashboard.py`), registered in `COMMANDS` like every other subcommand.
- **Foreground** (default): starts `http.server` on the configured port, prints `Dashboard running at http://127.0.0.1:8420` , blocks until `KeyboardInterrupt`, then shuts down cleanly.
- **`--daemon`**: installs an OS-native persistent job that runs `tokentracer dashboard` (foreground mode, as its own long-running process) at login and restarts it on crash — this is a second, separate scheduled-job identity from the collector's own `schedule`/`unschedule` feature (which runs `collect --lookback 1` once daily). Reuses the executable-resolution helper (PATH `tokentracer` vs. `sys.executable` + repo `tracker.py` fallback) being built for `schedule`/`unschedule` in `src/schedule.py`, but registers under its own job name so the two features can be toggled independently.
  - **macOS**: `~/Library/LaunchAgents/com.ai-token-tracer.dashboard.plist` with `RunAtLoad: true`, `KeepAlive: true` (restart on crash), stdout/stderr redirected to `~/.tokentracer/dashboard.log`. Installed via `launchctl unload` (ignore failure) then `launchctl load`.
  - **Windows**: Scheduled Task `ai-token-tracer-dashboard` with an at-logon trigger and restart-on-failure settings, action fixed to `tokentracer dashboard` (or `python`/`python3` + `tracker.py dashboard` fallback), created via `schtasks /Create /F ...`.
  - **Unsupported OS**: prints a clear error, returns non-zero — no guessing at a scheduling mechanism.
- **`--stop`**: removes the daemon (`launchctl unload` + delete plist, or `schtasks /Delete /F`); no-op with a friendly message if nothing is registered.
- Validation: `--port` must be 1–65535; invalid values produce a usage-style error before touching any socket or OS scheduler.

## API layer

`UsageReporter` (`src/report.py`) gains a `report_data(period, models, by_project, summary, detailed) -> dict` method mirroring `report()`'s signature, so the API layer calls it directly for JSON-serializable output instead of parsing `report(as_json=True))` strings. Each `ReportStrategy`'s row-computation logic is split out from its text-rendering (`render`) so `report_data()` and `render()` share the same underlying `rows`/aggregate computation without duplicating SQL.

New endpoints, backed by direct SQL against `usage.db` (read-only) rather than routed entirely through `UsageReporter`, since several dashboard views need shapes `UsageReporter` doesn't produce today:

| Endpoint | Purpose |
|---|---|
| `GET /api/summary?period=day\|week\|month\|year\|all\|custom&start=&end=&project=&source=` | Total tokens, per-source ("harness") breakdown, top models, context breakdown (input/output/cache-read/cache-creation/reasoning), session count, active-day count, first-collected date. |
| `GET /api/heatmap?days=180` | Per-day total tokens for the last N days, for the calendar heatmap. |
| `GET /api/trend?days=30` | Per-day totals broken down by `source`, for the stacked area chart. |
| `GET /api/projects?period=...` | Per-project total tokens, sorted descending, for the project list. |
| `GET /api/projects/{project}?period=...` | Single project's harness split + context breakdown (same shape as `/api/summary` scoped to one project). |
| `GET /api/sync-status` | `{ stores: [{ name, last_synced_at }] }` — `MAX(synced_at)` grouped by `store_name` from the `sync_log` table. Not part of `UsageReporter`; this is sync bookkeeping, not usage aggregation. |
| `GET /api/meta` | `{ most_recent_data_at }` — `MAX(end_ts)` across `sessions`, shown in the page header. |

**`week` and `custom` periods**: `week` is added as a new period alongside the existing `day`/`month`/`year`/`all` (last 7 days, analogous to `day`). `custom` bypasses the period enum entirely — `start`/`end` query params (ISO dates) are used directly as the date filter. This is a small, additive change to the date-filter logic already in `report.py`/the new SQL layer — no existing period's behavior changes.

**Harness cards are data-driven, not hardcoded**: the mockup design shows 8 harnesses (Claude, Codex, Cursor, OpenCode, Antigravity, Kilo-CLI, CodeBuddy, Copilot). TokenTracer has exactly two real collectors today (`claude_cli`, `copilot_cli`). `/api/summary`'s harness breakdown groups by whatever `source` values actually exist in `sessions` — so the frontend renders however many harness cards there is real data for (2, today), not a fixed list. No fake/placeholder harnesses are ever shown.

**Context breakdown is real token data only**: Input, Output, Cache Read, Cache Creation, Reasoning — pulled directly from existing `sessions` columns. The mockup's additional categories (System prompt, Custom agents, MCP servers, Skills) are not included: system-prompt tokens aren't exposed by the Anthropic usage API at all, and while subagent (`isSidechain`) and skill (`attributionSkill`) token attribution are technically derivable from raw Claude CLI JSONL, that requires new collector/schema work (tracking sidechain and attribution fields) that is out of scope for this dashboard — a candidate for a future collector enhancement, not this spec.

## Frontend

React + Vite source lives in `frontend/`, built via a one-time `npm install && npm run build` (documented in README), output directory gitignored — not committed, since the Python package stays stdlib-only at runtime and ships the *built* static assets, not a Node toolchain. `DashboardCommand`'s `http.server` serves the built `frontend/dist/` alongside the `/api/*` routes.

**Pages** (matches the shared design reference, `docs/design/dashboard.html`, scoped to real data per above):

- **Tokens page** (`/`, default): a stats card (7d/30d/avg/month token pills, top-models ranked list, active-days footer), an activity heatmap (`/api/heatmap`), a 30-day stacked usage trend by harness (`/api/trend`), a sync-log card (`/api/sync-status`), a total-tokens card with range tabs (Day/Week/Month/Total/Custom) and per-harness cards (`/api/summary`), a context-breakdown bar chart (Input/Output/Cache Read/Cache Creation/Reasoning), and a model-breakdown table.
- **Projects page** (`/projects`): a project list sorted by total tokens (`/api/projects`); selecting a project shows its harness split and context breakdown (`/api/projects/{project}`), mirroring the Tokens page's per-harness and context-breakdown components.
- A "Most recent data: {timestamp}" label (from `/api/meta`) appears in the header of both pages.
- Manual refresh only: each page has a Refresh button that re-fetches its endpoints; no auto-polling interval.
- Header shows a light/dark theme toggle (client-side only, no backend involvement).

## Error handling & testing

- API endpoints return `400` with a JSON error body for invalid query params (bad `period`, malformed `start`/`end` dates, unknown `project`); `500` with a generic JSON error body on unexpected SQL/IO failures — never a raw traceback in the response.
- `DashboardCommand` unit tests mock `platform.system()`/`subprocess.run` for `--daemon`/`--stop` (same pattern as `schedule`/`unschedule`'s tests: verify plist/task XML content, no real `launchctl`/`schtasks` invocation).
- API layer tests use a `tmp_path`-created SQLite db (matching existing `SqliteStore` test fixtures) seeded with known rows, asserting exact JSON shapes from each endpoint — including empty-db edge cases (no sessions yet: `/api/summary` returns zeros, not an error).
- No browser/E2E test suite in this repo; frontend correctness is verified manually (`npm run dev` against a seeded local db) before each release, per `CLAUDE.md`'s UI-testing guidance.

## Out of scope

- Authentication or network exposure beyond `127.0.0.1` — this is a single-user local tool.
- Auto-refresh/polling — manual refresh only, per explicit decision.
- System-prompt token tracking — not exposed by the underlying usage APIs at all.
- Subagent (sidechain) and skill (`attributionSkill`) token breakdown — technically derivable from raw Claude CLI JSONL but requires new collector/schema work; a candidate for a future, separate spec.
- MCP-server-specific breakdown — only tool-call *counts* per MCP server name are derivable (via `tool_use` block names), not token cost per call; not included in this dashboard.
- A `status` command to check whether the daemon is currently running (not requested; `--daemon` silently replaces any existing registration, matching `schedule`'s behavior).
- Linux/systemd support for `--daemon` (existing scheduling code only targets macOS + Windows; parity is the target, not expansion).
