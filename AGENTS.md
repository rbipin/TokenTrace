# AGENTS.md — Contributor & AI Agent Guide

This file documents the codebase for AI coding agents and human contributors.

---

## Project purpose

**ai-token** is a local, scheduled tracker for **GitHub Copilot CLI** token
usage on Windows. The Copilot CLI is the only surface that persists actual token
counts to disk, so the tracker focuses there. It records activity at a **daily
grain** in a local SQLite database so it can be rolled up by day, month, or year.

> **Why CLI-only:** VS Code and Visual Studio Copilot Chat show token/cost data
> only live in their Chat Debug Views and do **not** write it to disk, so they
> cannot be tracked locally. The earlier `vscode.py` and `visual_studio.py`
> collectors were removed because they could only capture activity counts, never
> real token usage. Re-add a collector only if a surface starts persisting token
> data.

---

## Repository layout

```
ai-token/
├── tracker.py                  # CLI entry point (collect / report subcommands)
├── requirements.txt            # pytest only; runtime uses stdlib exclusively
├── usage.db                    # SQLite database (gitignored, created on first run)
├── register-task.ps1           # PowerShell helper to register a Windows scheduled task
├── aitoken/
│   ├── __init__.py
│   ├── config.py               # Paths (copilot_home) + Config (db path, lookback)
│   ├── models.py               # ActivityRecord frozen dataclass + merge_records()
│   ├── pipeline.py             # TrackerPipeline — fluent builder, runs collectors in parallel
│   ├── store.py                # UsageStore — SQLite sink with idempotent upserts
│   ├── report.py               # UsageReporter (day/month/year roll-ups) + format_table()
│   └── collectors/
│       ├── __init__.py
│       ├── base.py             # ActivityCollector protocol + timestamp helpers
│       └── copilot_cli.py      # CopilotCliCollector (token usage + activity)
├── tests/
│   ├── conftest.py
│   ├── fixtures/               # Sample files used by tests
│   ├── test_cli_collector.py
│   ├── test_models.py
│   ├── test_pipeline.py
│   └── test_store_report.py
└── docs/plans/                 # Design documents
```

---

## Key abstractions

### `ActivityRecord` (`aitoken/models.py`)

Frozen dataclass. Primary key / storage grain is `(date, source, model, scope)`.
All counts (prompts, turns, tool_calls, *_tokens) are summed on `merge()`;
`context_peak_tokens` takes the maximum. Never pre-aggregate — store at daily
grain and roll up at read time.

### `ActivityCollector` protocol (`aitoken/collectors/base.py`)

```python
class ActivityCollector(Protocol):
    source: str                                         # stored in the source column
    def collect(self, since: date) -> Iterable[ActivityRecord]: ...
```

Collectors are **read-only** with respect to their source files. They must only
yield records dated on or after `since`.

### `TrackerPipeline` (`aitoken/pipeline.py`)

Fluent builder:

```python
TrackerPipeline()
    .add(collector)
    .since(start_date)
    .store(UsageStore(db_path))
    .run()           # -> RunResult(records_written, collectors_run, errors)
```

All collectors run in parallel via `ThreadPoolExecutor`. Errors from individual
collectors are captured in `RunResult.errors` rather than raising.

### `UsageStore` (`aitoken/store.py`)

SQLite sink. The upsert is fully idempotent: re-running `collect` for a date
overwrites that day's rows. Schema migrations add new columns via `ALTER TABLE`
and are applied on every `connect()`.

### `UsageReporter` (`aitoken/report.py`)

Pure read-time aggregation over `daily_activity`. Supports `period ∈ {day, month,
year}` with optional `source` and `model` filters. Returns `list[ReportRow]`.
`format_table()` renders a fixed-width text table.

---

## Data source

| Source identifier | Collector class | Location read |
|---|---|---|
| `copilot-cli` | `CopilotCliCollector` | `~/.copilot/session-store.db`, `session-state/<id>/events.jsonl`, `logs/process-*.log` |

**Copilot CLI token data**: completed sessions write a `session.shutdown` event
with `modelMetrics` (full per-model token breakdown: input, output, cache
read/write, reasoning). Active sessions fall back to summing `outputTokens` from
`assistant.message` events. Context-window peak is parsed from `Utilization …`
lines in `logs/process-*.log` and folded into the most-active row for that day.

**Why no VS Code / Visual Studio collectors**: those surfaces render token/cost
data only live in their Chat Debug Views and never persist it to disk, so token
usage cannot be read locally. They were removed deliberately — see the note at
the top of this file.

---

## Design principles

- **Open/Closed**: add a new surface by implementing `ActivityCollector` and
  calling `.add()` in `tracker.py`. No other module changes.
- **Idempotent collection**: every run re-computes days in the lookback window and
  overwrites stored rows. Safe to run on a schedule.
- **No runtime dependencies**: stdlib only (`sqlite3`, `json`, `pathlib`,
  `argparse`, `re`, `concurrent.futures`). `pytest` is dev-only.
- **SOLID + fluent style**: `TrackerPipeline` is the primary fluent API surface.
  Collectors are injected (DI), not instantiated internally.

---

## Running the tracker

```powershell
# Collect (default: re-scan last 3 days)
python tracker.py collect

# Collect with extended lookback
python tracker.py collect --lookback 30

# Report (day / month / year)
python tracker.py report --period day
python tracker.py report --period month --source copilot-cli
python tracker.py report --period year --json
```

Database defaults to `usage.db` next to `tracker.py`; override with `--db`.

---

## Running the tests

```powershell
pip install -r requirements.txt
python -m pytest -q
```

Tests use fixture files under `tests/fixtures/` and pass synthetic `Paths` via
`Config` to keep collectors hermetic — no live editor data is read during tests.

---

## Adding a new collector

1. Create `aitoken/collectors/<name>.py` implementing the `ActivityCollector`
   protocol (`source: str` attribute + `collect(since) -> Iterable[ActivityRecord]`).
2. Export it from `aitoken/collectors/__init__.py`.
3. Instantiate it in `_build_pipeline()` in `tracker.py` and add the relevant
   path to `Paths` in `aitoken/config.py`.
4. Add tests under `tests/` with fixture files for the new format.

Nothing else needs to change.
