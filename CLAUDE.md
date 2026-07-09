# CLAUDE.md — ai-token-tracer

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
python3 -m pytest -q

# Run a single test file
python3 -m pytest tests/test_claude_cli_collector.py -q

# Collect usage data (idempotent)
python3 tracker.py collect --lookback 3

# Report usage
# Default: today's sessions, one row each (Project Source Model Start End Input Output CacheRead CacheCreate CacheHit% Turns)
python3 tracker.py report

# --summary: compact session view (Session Project Date Start End Turns Tokens CacheHit%)
python3 tracker.py report --summary

# --summary + period: aggregated roll-up grouped by period+model
python3 tracker.py report --summary --period month
python3 tracker.py report --summary --period year
python3 tracker.py report --summary --period all     # entire database, no date filter

# --by-project: group by stored project identity (real name, guid, or whimsical mask)
python3 tracker.py report --summary --period all --by-project

# --period scopes all views: all | day | month | year  (default: day)
python3 tracker.py report --period month             # detailed sessions for current month
python3 tracker.py report --period all --by-project  # all projects ever

# Filter and JSON output
python3 tracker.py report --model claude-sonnet-4-6
python3 tracker.py report --summary --period month --json

# Configuration
python3 tracker.py config set track_project_names whimsical   # yes | no | whimsical
python3 tracker.py config set context work   # label this machine's usage ("work"/"personal")

# List local project identities (cwd -> guid -> whimsical name; never synced)
python3 tracker.py projects

# Sync unsynced records to configured remote stores (e.g., Supabase)
python3 tracker.py sync
python3 tracker.py sync --dry-run   # show pending counts without pushing
```

No build step — standard library only at runtime. Install `pytest` for testing: `pip install -r requirements.txt`.

**Packaging**: `pyproject.toml` defines the `tokentracer` console script. Install locally with `pipx install .` or `uv tool install .`. Default db path when installed: `~/.tokentracer/usage.db`.

`register-task.ps1` / `register-task.sh` are helpers to register the collector as a scheduled task (Windows Task Scheduler / macOS launchd).

## Architecture

The tracker follows an **Open/Closed pipeline**: adding a new data source only requires implementing the `ActivityCollector` protocol and registering it in `tracker.py`. No other module needs to change.

```
tracker.py               CLI entry point — builds argparse from the command registry and dispatches
src/
  commands/              Command pattern + static registry: one module per subcommand
    base.py              Command protocol (name, help, configure(parser), run(args) -> int)
    __init__.py          COMMANDS registry list (add new commands here)
    collect.py           CollectCommand (+ _build_pipeline / _build_stores helpers)
    report.py            ReportCommand
    config.py            ConfigCommand (owns its own set sub-dispatch)
    projects.py          ProjectsCommand
    sync.py              SyncCommand (+ _run_sync core logic)
    common.py            load_remote_stores helper shared by collect/sync
  models.py              SessionRecord frozen dataclass; merge_records deduplicates by (session_id, source, model)
  project_identity.py    ProjectIdentityStore (local-only project-key→guid→whimsical table; keys are repo slugs or folder names, legacy path keys migrated on init) + ProjectNameResolver (tri-state naming policy)
  repo_identity.py       resolve_repo_slug(cwd): walks up to .git, parses origin remote from config -> owner/repo (read-only, cached, never raises)
  collectors/
    base.py              ActivityCollector protocol + to_date / to_local_iso helpers
    copilot_cli.py       Reads session-store.db + events.jsonl from ~/.copilot/; yields per-(session, model) records
    claude_cli.py        Reads ~/.claude/projects/**/*.jsonl; yields one record per JSONL (session)
  whimsy/                Standalone docker-style name generator (stdlib-only, extractable to its own repo; only public API is generate_name)
  stores/
    __init__.py          SessionStore Protocol (name attr + upsert + close)
    registry.py          load_store_registry() (entry-point discovery + built-in fallback), instantiate_store()
    sqlite.py            SqliteStore — local SQLite sink; sessions table, idempotent upsert, sync tracking
    supabase.py          SupabaseStore — remote sink; upserts into a Supabase token_sessions table
  store.py               Deprecated alias for SqliteStore (kept for backward compat)
  pipeline.py            Fluent TrackerPipeline; runs collectors in parallel via ThreadPoolExecutor
  config.py              Paths, TOML loading (Config.load()), write_toml_setting(), _expand_env_vars()
  report.py              UsageReporter: all/day/month/year periods, cache efficiency header, default detailed session view, --summary, --by-project
```

**Data flow**: `Collector.collect(since)` → `List[SessionRecord]` → `merge_records` deduplicates → `UsageStore.upsert` writes SQLite → `UsageReporter.report` aggregates for display.

**Key invariants**:
- `collect` is always idempotent — re-running overwrites existing session rows. Merge key is `(session_id, source, model)`.
- Upsert is **last-write-wins** (INSERT OR REPLACE). There is no summation across runs.
- Collectors are **read-only** with respect to their source files — they must never write to them.

**UsageStore schema**: `sessions` table with PRIMARY KEY `(session_id, source, model)`. On first connect, if an old `usage` / `daily_activity` table exists it is dropped with a warning and the user is asked to re-collect. A separate local-only `project_identities` table stores normalized cwd → guid → whimsical-name mappings and is never queried by sync code.

**Config file**: `~/.tokentracer.toml`. `[tracking]` supports `track_project_names` as a string enum: `"yes"` (real name), `"no"` (stable 12-hex guid per cwd; default), or `"whimsical"` (stable docker-style masked name). Project identity is keyed by git repo slug (`owner/repo` — from the Copilot session `repository` column, or by reading `<cwd>/.git/config` origin remote for Claude sessions), falling back to the cwd folder name; full paths are never stored. Two clones of the same repo therefore map to one project. `yes` mode displays the full slug (e.g. `rbipin/TokenTrace`). `tracker.py config set track_project_names <value>` validates against that enum; invalid or legacy boolean TOML values warn and fall back to `"no"`. The `collect` CLI overrides it per run with `--project-mode <yes|no|whimsical>`. `[tracking]` also supports `context` (string, default `"personal"`) — a usage-context label (e.g. `"work"`) stamped on every collected `SessionRecord` via `TrackerPipeline.context()` and stored in the `context` column of the `sessions` table (and pushed to remote stores); CLI flag `--context <label>` on `collect` overrides it per run. `[stores.<name>]` sections declare remote stores (see below); `${VAR}` placeholders in string values are expanded at instantiation time — lookup order is `os.environ` first, then `~/.tokentracer.env` (simple `KEY=VALUE` file, `#` comments, optional quotes). Missing vars raise `ValueError`.

## Stores registry (remote sinks)

Stores implement the `SessionStore` Protocol in `src/stores/__init__.py` (`name: str` class attr, `upsert(records) -> int`, `close()`). The local `SqliteStore` is always active; remote stores are optional and driven by `tokentracer sync`:

- **Discovery**: `load_store_registry()` in `src/stores/registry.py` discovers stores via the `tokentracer.stores` entry-point group (declared in `pyproject.toml`). When the package isn't installed (repo checkout), it falls back to the built-ins: `sqlite` and `supabase`.
- **Instantiation**: `instantiate_store(name, params, class_path=None)` expands `${VAR}` env placeholders in `params`, then constructs the store — via `class_path` (dotted import path, bypasses the registry) if given, else by registry name.
- **Sync flow**: `tracker.py sync` reads `[stores.*]` from `~/.tokentracer.toml`, and for each remote store pushes rows the SqliteStore reports as unsynced (`unsynced_for(store_name)`), then marks them synced per store. `--dry-run` prints pending counts only. Failed stores are reported without blocking others; stores are always closed in a `finally`.

**Built-in remote store — Supabase** (`src/stores/supabase.py`): upserts rows into a `token_sessions` table with `on_conflict="session_id,source,model"` (mirrors the local primary key). Lazy client creation on first upsert; requires optional dep `supabase>=2.0` (`pip install tokentracer[supabase]`). Configure with:

```toml
[stores.supabase]
url = "${SUPABASE_URL}"
key = "${SUPABASE_KEY}"      # service role key (bypasses RLS)
table = "token_sessions"     # optional, this is the default
```

### Adding a new store

1. Create `src/stores/<name>.py` with a class implementing `SessionStore` (`name` class attr + `upsert` + `close`). Keep third-party imports optional (try/except at module level, raise a helpful `ImportError` on first use).
2. Register it under `[project.entry-points."tokentracer.stores"]` in `pyproject.toml` (and add it to the built-in fallback in `registry.py` if it ships with this repo).
3. Add any third-party dep as an optional extra in `[project.optional-dependencies]`.
4. Add tests under `tests/` mocking the client (see `tests/test_supabase_store.py`).
5. Users enable it with a `[stores.<name>]` section in `~/.tokentracer.toml`; external packages can also provide stores via the same entry-point group — no code changes here needed.

**Copilot CLI data details**: newer CLI versions nest event payloads under a `data` key and use `created_at`/`updated_at` session columns (older: `startedAt`/`endedAt`) — the collector supports both. Completed sessions write a `session.shutdown` event with `modelMetrics` (per-model token breakdown; counts flat in old format, under `usage` + `requests.count` in new). Active sessions fall back to summing `outputTokens` from `assistant.message` events (new format exposes only output tokens there; full input/cache counts arrive at shutdown). Each `(session_id, model)` pair becomes one `SessionRecord`.

**Claude CLI data details**: each conversation is a JSONL file under `~/.claude/projects/<project-id>/<conv-id>.jsonl`. The file stem is the `session_id`. Assistant messages contain `message.usage` with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`. One `SessionRecord` per JSONL file.

**No VS Code / Web / Desktop collectors**: those surfaces render token data only live and never persist it to disk. Do not add a collector for a surface unless it starts persisting token data to disk.

**DB default**: `usage.db` next to `tracker.py` (override with `--db`). `Config.paths` uses `Path.home()` for cross-platform compatibility; tests inject synthetic `Paths` via `Config` to stay hermetic.

## Adding a new collector

1. Create `src/collectors/<name>.py` implementing `ActivityCollector` (`source: str` class attr + `collect(since: date) -> Iterable[SessionRecord]`).
2. Export it from `src/collectors/__init__.py`.
3. Add the relevant path to `Paths` in `src/config.py`.
4. Instantiate it in `_build_pipeline()` in `src/commands/collect.py`.
5. Add tests under `tests/` using `tmp_path` to create fixture files.

Nothing else needs to change.
