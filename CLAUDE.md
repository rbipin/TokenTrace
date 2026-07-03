# CLAUDE.md

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
python3 tracker.py report --period day
python3 tracker.py report --period month --json
```

No build step — standard library only at runtime. Install `pytest` for testing: `pip install -r requirements.txt`.

`register-task.ps1` is a PowerShell helper to register the collector as a Windows scheduled task.

## Architecture

The tracker follows an **Open/Closed pipeline**: adding a new data source only requires implementing the `ActivityCollector` protocol and registering it in `tracker.py`. No other module needs to change.

```
tracker.py               CLI entry point (collect / report subcommands)
src/
  models.py              ActivityRecord frozen dataclass; merge_records deduplicates by (date, source, model, scope)
  collectors/
    base.py              ActivityCollector protocol + date helpers
    copilot_cli.py       Reads session-store.db + events.jsonl + process logs from ~/.copilot/
    claude_cli.py        Reads ~/.claude/projects/**/*.jsonl; yields per-(date, model) token counts
  store.py               SQLite-backed UsageStore; upsert is idempotent (day-grain keyed)
  pipeline.py            Fluent TrackerPipeline; runs collectors in parallel via ThreadPoolExecutor
  config.py              Paths (copilot_home, claude_projects) and defaults (db next to tracker.py)
  report.py              UsageReporter: day/month/year roll-ups via GROUP BY
```

**Data flow**: `Collector.collect(since)` → `List[ActivityRecord]` → `merge_records` deduplicates → `UsageStore.upsert` writes SQLite → `UsageReporter.report` aggregates for display.

**Key invariants**:
- `collect` is always idempotent — re-running overwrites existing day rows. Merge key is `(date, source, model, scope)`.
- On merge, all counts (prompts, turns, tokens) are **summed**; `context_peak_tokens` takes the **maximum**.
- Collectors are **read-only** with respect to their source files — they must never write to them.

**UsageStore schema migrations**: new columns are added via `ALTER TABLE` applied on every `connect()`, so the schema evolves safely across versions.

**Copilot CLI data details**: completed sessions write a `session.shutdown` event with `modelMetrics` (full per-model token breakdown). Active sessions fall back to summing `outputTokens` from `assistant.message` events. Context-window peak is parsed from `Utilization …` lines in `logs/process-*.log`.

**Claude CLI data details**: each conversation is a JSONL file under `~/.claude/projects/<project-id>/<conv-id>.jsonl`. Assistant messages contain `message.usage` with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`. The collector aggregates by `(date, model)` across all projects.

**No VS Code / Web / Desktop collectors**: those surfaces render token data only live and never persist it to disk. Do not add a collector for a surface unless it starts persisting token data to disk.

**DB default**: `usage.db` next to `tracker.py` (override with `--db`). `Config.paths` uses `Path.home()` for cross-platform compatibility; tests inject synthetic `Paths` via `Config` to stay hermetic.

## Adding a new collector

1. Create `src/collectors/<name>.py` implementing `ActivityCollector` (`source: str` class attr + `collect(since: date) -> Iterable[ActivityRecord]`).
2. Export it from `src/collectors/__init__.py`.
3. Add the relevant path to `Paths` in `src/config.py`.
4. Instantiate it in `_build_pipeline()` in `tracker.py`.
5. Add tests under `tests/` using `tmp_path` to create fixture files.

Nothing else needs to change.
