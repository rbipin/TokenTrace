# Dashboard Redesign ("Token Trace") — Design Spec

Date: 2026-07-24

## Context

The local dashboard (`tracker.py dashboard`, served from `src/dashboard/` and the `frontend/` React app) currently uses a utilitarian dark/light theme (`frontend/src/App.css`). A target visual design exists at `docs/design/dashboard.html` (a self-extracting bundled mockup, branded "Token Trace"): a denser, near-black/green "product" aesthetic with a hero total-tokens number, range tabs, rolling-window stat cards, restyled harness/model breakdowns, and a richer By Project detail panel.

The current app already shares the same structural bones as the target (sidebar nav, cards, heatmap, harness grid, bar rows, project list) — this is a layout-and-visual redesign of an existing app, not a rebuild from scratch.

## Scope

- Full layout match to `docs/design/dashboard.html`: branding header, hero "Total Tokens" number, range tabs, fixed rolling-stat row, restyled cards, and a restructured By Project detail panel.
- Range tabs: **Day / Week / Month / Total**, all fully functional, backed by the existing `day` / `week` / `month` / `all` periods in `src/dashboard/queries.py`. No Custom tab, no date-range picker.
- The rolling-stat row (7d / 30d / avg / month totals, "Started" date, "Active days" count) is **fixed** — it always shows these rolling windows regardless of which range tab is selected. Only the hero "Total Tokens" number, the context-breakdown bar, and the model/harness breakdowns react to the selected range tab.
- Model breakdown: a single consolidated table only (drop the compact top-3 mini-list variant seen in the reference design).
- The segmented gradient bar under the hero number represents **Context Breakdown** proportions (Messages / Tool calls / Reasoning / System prompt / Custom agents / MCP servers / Skills) — not harness split.
- **Out of scope**: "Limits" sidebar nav item, "Share" header button, custom date-range picking. None of these are built, not even as disabled placeholders.
- No new pages beyond the existing Tokens and By Project pages.
- Tech stack: introduce Tailwind CSS into the `frontend/` Vite build, replacing the hand-written `App.css` rules.

## Backend changes (`src/dashboard/queries.py`, `src/dashboard/server.py`)

- Extend `summary()` to add a `rolling` key to its return dict:
  ```python
  "rolling": {
      "7d": {"total_tokens": int},
      "30d": {"total_tokens": int, "active_days": int},
      "avg_per_active_day": float,  # 30d total_tokens / 30d active_days (0.0 if active_days == 0)
  }
  ```
  Computed independently of the `period` argument, reusing the existing `_last_n_days_filter(days)` helper (already used by `heatmap()` and `trend()`) for the 7-day and 30-day windows.
- No new HTTP endpoints. `/api/summary` already accepts `period`/`start`/`end`/`project`/`source` and returns `total_tokens`, `active_days`, `first_date`, `harnesses`, `models` — it just gains the `rolling` block.
- `/api/projects/detail` already returns a full `summary()` shape per project (via `project_detail()` → `summary()`), so it automatically gains the same `rolling` block. The By Project detail panel does not use `rolling`, but this is harmless — no special-casing needed.
- `meta()` and `sync_status()` are unchanged; they already back the header "most recent data" badge and the Sync Log card.

## Frontend changes (`frontend/`)

### Build/styling

- Add `tailwindcss` (with `@tailwindcss/vite` or PostCSS integration) to `frontend/`.
- Replace `App.css`'s hand-written rules with Tailwind utility classes, plus a small Tailwind config extension for the design's palette (near-black backgrounds `#0a0b0d` / `#12141a`, green accent `#22c55e`, purple accent `#a78bfa` for tool-call segments, etc.) and type scale.
- The existing `data-theme="dark"|"light"` toggle on `<html>` is preserved, expressed via Tailwind's dark-mode variant mechanism. The reference design is dark-only; the light theme keeps using the current light palette values as a baseline, adapted to the new layout.

### Components

- `App.jsx` — add a sidebar branding header (logo mark + "Token Trace" wordmark). Nav stays Tokens + By Project only (no Limits item).
- New `RangeTabs.jsx` — Day/Week/Month/Total selector; lifts `period` state up to the owning page.
- New `StatsRow.jsx` — renders the fixed 7d/30d/avg/month/Started/Active-days cards from `summary().rolling`, `summary().active_days`, and `summary().first_date`.
- `TokensPage.jsx` — hosts the hero "Total Tokens" number and the Context Breakdown segmented bar beneath it (both driven by the selected range tab); header gains a "Most recent data: <timestamp> (<Xm> ago)" badge with relative-time formatting, sourced from `/api/meta`.
- `HarnessCards.jsx` — restyled into the larger icon+percentage grid layout from the reference design; same data shape (`summary().harnesses`).
- `ModelTable.jsx` — restyled as the single consolidated breakdown list; no mini-list mode.
- `Heatmap.jsx`, `TrendChart.jsx`, `SyncLogCard.jsx` — visual restyle only; props/data unchanged.
- New `HarnessSplit.jsx` — used on the By Project detail panel; percentage list by source, styled like `ContextBreakdown` but keyed off `summary().harnesses` for the selected project.
- `ProjectsPage.jsx` — restructured into a left "Top projects" list (existing `ProjectList`, restyled) and a right detail panel for the selected project: total tokens, `HarnessSplit`, `ContextBreakdown`.
- Shared `formatTokens()` helper — returns both the full comma-formatted number and an abbreviated form (e.g. `263.7B`); used consistently by the hero number, stat row, and breakdown rows.

## Testing

- Backend: extend the dashboard queries test suite with cases for the new `rolling` block in `summary()` — 7d/30d totals, `avg_per_active_day` including the zero-active-days edge case — and confirm existing `period`/`harnesses`/`models`/`active_days`/`first_date` behavior is unchanged.
- Frontend: this repo has no JS test suite (Python-stdlib-only project). Verify manually by running `tracker.py dashboard` and comparing both the Tokens and By Project pages, in both light and dark mode, against `docs/design/dashboard.html`.
- Run `python3 -m pytest -q` to confirm no regressions elsewhere.
