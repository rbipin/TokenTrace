# Design History

Consolidated record of the design specs and implementation plans that shaped
TokenTracer, in chronological order. Each entry summarizes the problem, key
decisions, and outcome. All features below are implemented; for current
behavior see [ARCHITECTURE.md](./ARCHITECTURE.md) and `CLAUDE.md`.

---

## 2026-06-15 — Copilot Usage Tracker (initial design)

The original tool: a periodic, fully local Python tracker for GitHub Copilot
activity, stored at a daily grain (`date × source × model × scope`) for
day/month/year/model rollups via read-time `GROUP BY`.

Key decisions:

- **Token counts out of scope** initially — VS Code Chat and the CLI did not
  persist them to disk (only shown live in memory). Activity metrics + CLI
  context-window peaks were the only reliable local signals.
- **GitHub billing API out of scope** — org-managed Copilot; no billing access.
- **SOLID collector-per-surface architecture**: `ActivityCollector` protocol,
  one collector per surface, fluent `TrackerPipeline`, SQLite `UsageStore`
  with idempotent upsert, `UsageReporter` for rollups. Stdlib-only, read-only
  access to editor data.
- Planned collectors: VS Code Chat, Copilot CLI, Visual Studio (best-effort
  no-op). The VS Code / Visual Studio collectors were later dropped when the
  project pivoted to token tracking — those surfaces never persist token data.

## 2026-07-03 — Claude CLI Token Tracker

Added Claude Code CLI token tracking from `~/.claude/projects/**/*.jsonl`
(cross-platform via `Path.home()`), the project's first true *token* source.

- Assistant messages carry `message.usage` with `input_tokens`,
  `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`.
- Mapped into the existing record model with a `claude-cli` source; grouped by
  `(date, model)` at collect time (day grain, pre-session refactor).
- Collector auto-skips when `~/.claude/projects/` is absent; mtime fast-path
  filter against the lookback window; hermetic `tmp_path` tests.

## 2026-07-05 — Internal Refactor (report strategies)

Internal restructuring with frozen CLI/output behavior:

- `ReportContext` frozen dataclass replaced a 7-argument parameter clump.
- `ReportStrategy` protocol + four view classes (`SessionsDetailedView`,
  `PeriodSummaryView`, `SessionsListView`, `ByProjectView`) replaced
  boolean-flag dispatch, selected via a `(summary, by_project)` dispatch table.
- `_format_output()` unified copy-pasted JSON/table logic.
- `write_toml_setting` split into `_write_toml_311` / `_write_toml_legacy`;
  flag normalization in `tracker.py` extracted into named helpers.

## 2026-07-05 — Session-Primary Tracking

The pivotal refactor from day-grain to **session-grain** storage. Each record
is one conversation session; day/month aggregates are derived at query time.

- `ActivityRecord` → `SessionRecord` (frozen dataclass) with `session_id`,
  `start_ts`/`end_ts`, `project`, `turns`, and full token breakdown
  (input/output/cache-creation/cache-read/context-peak). `scope` and `prompts`
  removed.
- Merge key `(session_id, source)` (later widened to include `model`);
  last-write-wins upsert — safe because source data is immutable, making
  `collect` idempotent and backfill just a `--lookback` re-collect.
- New `sessions` table replaced `usage` (old table dropped with a warning).
- Introduced `~/.tokentracer.toml` (`[tracking] track_project_names`, then a
  boolean) with CLI-flag > toml > default precedence.
- New reporting: cache-efficiency header
  (`cache_read / (input + cache_read + cache_creation)`), `--by-project`, and
  session-level views.

## 2026-07-06 — Pluggable Store Interface

Multi-backend storage: SQLite always-on locally, remote stores as opt-in push
destinations (draft + approved design).

- `SessionStore` protocol (`name`, `upsert(records) -> int`, `close()`),
  duck-typed like `ActivityCollector`.
- Discovery via the `tokentracer.stores` entry-point group with a built-in
  fallback for repo checkouts; `class =` dotted-path escape hatch in config.
- `TrackerPipeline.stores(*stores)` with SQLite first (must succeed) and
  remotes pushed in parallel, log-and-continue on failure; `.store()` kept as
  a deprecated alias (`src/store.py` shim).
- `sync_log` table keyed `(session_id, source, model, store_name)` tracks
  per-store sync state; `tokentracer sync` pushes unsynced rows per store,
  with `--dry-run` showing pending counts.
- Config activation via `[stores.<name>]` sections in `~/.tokentracer.toml`.

## 2026-07-06 — Supabase Remote Store

First remote store: `SupabaseStore` plugging into the registry with zero
pipeline/CLI changes.

- Upserts into a `token_sessions` table with
  `on_conflict="session_id,source,model"` (mirrors the local primary key), so
  sync is idempotent. Lazy, cached client; service-role key (bypasses RLS).
- `${VAR}` env-var expansion added to `instantiate_store()` for all stores
  (later extended to read `~/.tokentracer.env` after `os.environ`); missing
  vars raise `ValueError` before any network call.
- `supabase>=2.0` as an optional extra (`pip install tokentracer[supabase]`).

## 2026-07-07 — Architecture Documentation

Produced `docs/ARCHITECTURE.md`: contributor-facing end-to-end description of
the collect flow, exact source files each collector reads (both Copilot CLI
schema variants, Claude JSONL), storage schema, report flow, configuration,
and the stores registry — with Mermaid diagrams, linked from the README.
Docs-only; every path/schema detail cross-checked against source.

## 2026-07-07 — Register Scripts: Clean Replace

Re-running `register-task.ps1` / `register-task.sh` must fully replace any
existing scheduled task, never partially update or duplicate:

- PS1: dropped the existence-check/`Set-ScheduledTask` branch in favor of
  `Register-ScheduledTask -Force` (replace-or-create).
- SH: already clean-replace (unload → overwrite plist → load); message
  aligned. Validation via parse checks only (no automated tests for
  environment-dependent scripts).

## 2026-07-07 — Release Pipeline

GitHub Actions CI + tag-triggered releases (no PyPI):

- `ci.yml`: pytest on `ubuntu-latest` / Python 3.11, triggered by push to
  `main`, PRs, and `workflow_call`. Single OS/version — pure-stdlib project
  with hermetic tests.
- `release.yml`: on `v*` tags, reuses CI, verifies the tag matches
  `pyproject.toml` version (hand-maintained; fails on mismatch), builds wheel
  + sdist with `python -m build`, publishes via
  `gh release create --generate-notes`.
- Install documented via wheel URL (`uv tool install` / `pip install`) or
  `git+https://...@vX.Y.Z`.

## 2026-07-08 — Project Name Masking (tri-state)

`track_project_names` became a string enum — an intentional breaking change
from the old boolean:

- `"yes"` — real project name; `"no"` (default) — stable opaque guid (first
  12 hex of a uuid4); `"whimsical"` — stable docker-style masked name.
- New `project_identities` table (key → guid → whimsical name) in the same
  `usage.db`, **local-only by construction** — never queried by any sync path.
- `ProjectNameResolver` centralizes the tri-state policy (mode branching,
  identity-store calls, warn-and-`None` error fallback); collectors receive it
  by constructor injection and know nothing about modes.
- `src/whimsy/` built as a standalone stdlib-only package (public API:
  `generate_name(existing) -> str`) porting Docker's Apache-2.0
  `namesgenerator` word lists with attribution (`LICENSE-NOTICE.md`), plus
  supplementary annotated surnames from `docs/new-names.txt` (duplicates of
  the Docker list skipped). Collision retry with numeric-suffix fallback.
  Deliberately imports nothing from `src/` so it can be extracted to its own
  repo by directory copy.
- CLI: `--project-mode {yes,no,whimsical}` replaced the boolean flag pair;
  `config set track_project_names` validates the enum.

## 2026-07-09 — Repo-Identity Project Names

Project identity re-keyed from full cwd paths to **git repo slugs**
(`owner/repo`), with folder-name fallback — display/cleanliness driven:

- New `src/repo_identity.py`: `resolve_repo_slug(cwd)` walks up to `.git`
  (including worktree `gitdir:` pointers), parses the origin remote with
  `configparser`, normalizes https/ssh/scp URL forms host-agnostically to the
  last two path segments. Never raises; read-only; LRU-cached.
- Copilot uses the session `repository` column when present; Claude reads
  `<cwd>/.git/config`. Collectors pass the slug as both display name and
  identity key; two clones of one repo collapse into one project. `yes` mode
  shows the full slug.
- One-time idempotent migration in `ProjectIdentityStore.__init__` re-keys
  path-looking rows to slugs/folder names, preserving guids and whimsical
  names; on collision the older row wins. Historical display drift accepted.

## 2026-07-09 — Context Peak Tokens

Populated the long-dormant `context_peak_tokens` field: the largest single
API-request footprint in a session (`prompt + output` tokens, exact
API-reported numbers), captured at collect time because source files are
eventually pruned and a max cannot be recovered from stored sums.

- **Copilot**: bulk `MAX(input_tokens + output_tokens)` query over the
  `assistant_usage_events` table in `session-store.db`, `WHERE agent_id IS
  NULL` (subagents excluded; `input_tokens` there is cache-inclusive),
  grouped by `(session_id, model)`. Missing table → peaks stay `0` silently.
- **Claude**: running max of per-message
  `input + cache_read + cache_creation + output` (Claude's `input_tokens`
  excludes cache) in the existing parse loop — no new I/O.
- `CtxPeak` column added to the detailed report view; field added to the
  Supabase upsert payload.

## 2026-07-09 — Tool Calls & Reasoning Tokens

Filled the remaining dormant fields and completed remote payloads:

- **Copilot**: `tool.execution_complete` events counted per model (event scan
  no longer short-circuits at `session.shutdown` — tool counts exist only as
  discrete events, not in `modelMetrics`); attributed per event `model` when a
  shutdown record exists, else summed to the single detected model.
  `reasoning_tokens` was already collected — it just wasn't displayed.
- **Claude**: `tool_use` content blocks counted per assistant message;
  reasoning stays `0` by design (thinking tokens fold into `output_tokens`).
- **Report**: `Reasoning` and `Tools` columns in the detailed session view;
  new `report --detailed` strategy — every DB row, every column, plus a
  `Synced` column from `sync_log` (LEFT JOIN / GROUP_CONCAT), overriding
  `--summary`/`--by-project` and ignoring `--period`.
- **Supabase**: upsert payload gained `tool_calls`, `reasoning_tokens`,
  `context_peak_tokens` (remote table needs matching bigint columns).
- No backfill tooling: idempotent `collect --lookback N` re-collects sessions
  still on disk.

---

## Standing invariants established across these designs

- Collectors are **read-only** against their sources; `collect` is idempotent
  (last-write-wins upsert keyed `(session_id, source, model)`).
- Stdlib-only at runtime; third-party deps only as optional store extras.
- No VS Code / Web / Desktop collectors — those surfaces never persist token
  data to disk.
- `project_identities` is local-only and excluded from sync by construction.
- Adding a collector or store requires no changes outside its own module plus
  registration (Open/Closed pipeline).
