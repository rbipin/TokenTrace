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

# --by-project: group by project (requires --track-projects data in db)
python3 tracker.py report --summary --period all --by-project

# --period scopes all views: all | day | month | year  (default: day)
python3 tracker.py report --period month             # detailed sessions for current month
python3 tracker.py report --period all --by-project  # all projects ever

# Filter and JSON output
python3 tracker.py report --model claude-sonnet-4-6
python3 tracker.py report --summary --period month --json

# Configuration
python3 tracker.py config set track_project_names true
```

No build step — standard library only at runtime. Install `pytest` for testing: `pip install -r requirements.txt`.

**Packaging**: `pyproject.toml` defines the `tokentracer` console script. Install locally with `pipx install .` or `uv tool install .`. Default db path when installed: `~/.tokentracer/usage.db`.

`register-task.ps1` / `register-task.sh` are helpers to register the collector as a scheduled task (Windows Task Scheduler / macOS launchd).

## Architecture

The tracker follows an **Open/Closed pipeline**: adding a new data source only requires implementing the `ActivityCollector` protocol and registering it in `tracker.py`. No other module needs to change.

```
tracker.py               CLI entry point (collect / report / config subcommands)
src/
  models.py              SessionRecord frozen dataclass; merge_records deduplicates by (session_id, source, model)
  collectors/
    base.py              ActivityCollector protocol + to_date / to_local_iso helpers
    copilot_cli.py       Reads session-store.db + events.jsonl from ~/.copilot/; yields per-(session, model) records
    claude_cli.py        Reads ~/.claude/projects/**/*.jsonl; yields one record per JSONL (session)
  store.py               SQLite-backed UsageStore; sessions table, upsert is idempotent (session-grain keyed)
  pipeline.py            Fluent TrackerPipeline; runs collectors in parallel via ThreadPoolExecutor
  config.py              Paths, TOML loading (Config.load()), write_toml_setting()
  report.py              UsageReporter: all/day/month/year periods, cache efficiency header, default detailed session view, --summary, --by-project
```

**Data flow**: `Collector.collect(since)` → `List[SessionRecord]` → `merge_records` deduplicates → `UsageStore.upsert` writes SQLite → `UsageReporter.report` aggregates for display.

**Key invariants**:
- `collect` is always idempotent — re-running overwrites existing session rows. Merge key is `(session_id, source, model)`.
- Upsert is **last-write-wins** (INSERT OR REPLACE). There is no summation across runs.
- Collectors are **read-only** with respect to their source files — they must never write to them.

**UsageStore schema**: `sessions` table with PRIMARY KEY `(session_id, source, model)`. On first connect, if an old `usage` / `daily_activity` table exists it is dropped with a warning and the user is asked to re-collect.

**Config file**: `~/.tokentracer.toml` under `[tracking]`. Supported keys: `track_project_names` (bool, default false). CLI flag `--track-projects` / `--no-track-projects` overrides the TOML value per run.

**Copilot CLI data details**: completed sessions write a `session.shutdown` event with `modelMetrics` (full per-model token breakdown). Active sessions fall back to summing `outputTokens` from `assistant.message` events. Each `(session_id, model)` pair becomes one `SessionRecord`.

**Claude CLI data details**: each conversation is a JSONL file under `~/.claude/projects/<project-id>/<conv-id>.jsonl`. The file stem is the `session_id`. Assistant messages contain `message.usage` with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`. One `SessionRecord` per JSONL file.

**No VS Code / Web / Desktop collectors**: those surfaces render token data only live and never persist it to disk. Do not add a collector for a surface unless it starts persisting token data to disk.

**DB default**: `usage.db` next to `tracker.py` (override with `--db`). `Config.paths` uses `Path.home()` for cross-platform compatibility; tests inject synthetic `Paths` via `Config` to stay hermetic.

## Adding a new collector

1. Create `src/collectors/<name>.py` implementing `ActivityCollector` (`source: str` class attr + `collect(since: date) -> Iterable[SessionRecord]`).
2. Export it from `src/collectors/__init__.py`.
3. Add the relevant path to `Paths` in `src/config.py`.
4. Instantiate it in `_build_pipeline()` in `tracker.py`.
5. Add tests under `tests/` using `tmp_path` to create fixture files.

Nothing else needs to change.
