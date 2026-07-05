# Token Trace

A local, periodic tracker for your **AI tool token usage** (GitHub Copilot CLI
and Claude Code CLI). It records activity at a **session grain** (one row per
session per model) so you can roll it up by day, month, or year — and drill
into individual sessions or projects.

## Purpose

I use AI tools heavily from the CLI and noticed that none of the existing AI harnesses — Copilot, Claude Code, etc. — give a meaningful token-level breakdown or trend over time. They show activity in the moment but offer no persistent view of how much you're consuming or how efficiently you're using it. No dashboard exists today that tracks and reports this across tools in one place.

This project is my answer to that gap: a lightweight local collector that pulls token data from the sources that actually persist it to disk, stores it in SQLite, and lets me query it however I want. The goal is to understand my AI token usage and how it trends over time — and eventually use the data for things like a heatmap of usage intensity across days or models.

**Built entirely with Claude Code** — requirements, direction, and corrections were provided by me; implementation was handled by Claude.

## Outcome

- Know exactly how many tokens I'm consuming per session, per model, per tool — not a rough estimate, actual counts
- See cache efficiency: how much of your input came from cache and how much cost that saved
- Spot trends: am I using more tokens this month than last? Is one model eating disproportionately more?
- See which projects or contexts drive the heaviest usage (opt-in)
- Have raw data ready for a heatmap (usage intensity by day) when I get around to building the visualisation layer

> **Why only CLI surfaces?**
>
> These are the only surfaces that write **actual token counts** to disk.
> Copilot CLI records them via the `session.shutdown` event's `modelMetrics`.
> Claude Code CLI records them in per-conversation JSONL files under
> `~/.claude/projects/`. VS Code and web UIs render token data live but do not
> persist it locally. See `docs/plans/2026-06-15-copilot-usage-tracker-design.md`
> for the original rationale.

## Data sources

| Source | Location | Metrics |
|---|---|---|
| Copilot CLI | `~/.copilot/session-store.db` + `session-state/<id>/events.jsonl` | sessions, turns, per-model token counts (input, output, cache read/write, reasoning) |
| Claude Code CLI | `~/.claude/projects/**/*.jsonl` | per-session token counts (input, output, cache read/write) |

## Requirements

- Python 3.11+ (standard library only at runtime; `tomllib` used for config).
- `pytest` only for running the tests: `pip install -r requirements.txt`.

## Usage

```bash
# Collect (the scheduled job). Re-scans the last N days and upserts.
python3 tracker.py collect                      # default lookback: 3 days
python3 tracker.py collect --lookback 30        # backfill more history
python3 tracker.py collect --track-projects     # store project names this run
python3 tracker.py collect --no-track-projects  # suppress project names this run

# Report — roll up by day / month / year
python3 tracker.py report --period day
python3 tracker.py report --period month
python3 tracker.py report --period year

# Drill into individual sessions
python3 tracker.py report --period month --sessions

# Group by project (requires --track-projects to have been used during collect)
python3 tracker.py report --period month --by-project

# Filter by model and/or emit JSON
python3 tracker.py report --period month --model claude-sonnet-4-6
python3 tracker.py report --period year --json

# Configuration (persisted to ~/.tokentracer.toml)
python3 tracker.py config set track_project_names true
```

The database lives at `usage.db` next to `tracker.py` by default (override
with `--db`). Re-running `collect` is **idempotent** — each session is
identified by its unique ID and re-collecting overwrites the stored row.

### Cache efficiency

Every text report opens with a cache efficiency summary:

```
Cache efficiency: 72% read from cache (~65% cost saved)
```

This is computed across all sessions in the database and shows what fraction of
your total token budget came from the cache, and the approximate cost saving
(cache reads cost ~10% of regular input tokens).

## Configuration

Project-name tracking is opt-in to avoid storing path information you might
not want persisted. Enable it persistently:

```bash
python3 tracker.py config set track_project_names true
```

This writes `[tracking]\ntrack_project_names = true` to `~/.tokentracer.toml`.
You can also override per-run with `--track-projects` / `--no-track-projects`.

## Run it periodically

**Windows (Task Scheduler):**

```powershell
$python = (Get-Command python).Source
$script = "C:\path\to\ai-token\tracker.py"

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" collect"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
             -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName "ai-token-tracer" `
  -Action $action -Trigger $trigger -Settings $settings
```

To remove it: `Unregister-ScheduledTask -TaskName "ai-token-tracer"`.

**macOS (launchd):** create a plist in `~/Library/LaunchAgents/` that runs
`python3 /path/to/tracker.py collect` on an hourly interval.

## Project layout

```
ai-token/
├─ tracker.py                # CLI entry (collect / report / config)
├─ src/
│  ├─ models.py             # SessionRecord (frozen dataclass) + merge
│  ├─ collectors/           # one collector per AI tool surface
│  │  ├─ base.py            # ActivityCollector protocol + to_date / to_local_iso helpers
│  │  ├─ copilot_cli.py     # Copilot CLI — one record per (session, model)
│  │  └─ claude_cli.py      # Claude Code CLI — one record per JSONL session
│  ├─ store.py              # UsageStore (sqlite, session-primary, idempotent upsert)
│  ├─ report.py             # UsageReporter (day/month/year, cache efficiency, --sessions, --by-project)
│  ├─ pipeline.py           # fluent TrackerPipeline
│  └─ config.py             # Paths, TOML loading, write_toml_setting
├─ tests/                   # pytest suite with fixtures
└─ docs/                    # design docs and implementation plans
```

## Development

```bash
pip install -r requirements.txt
python3 -m pytest -q
```

## Extending

Add a new surface by implementing the `ActivityCollector` protocol
(`collect(since: date) -> Iterable[SessionRecord]`) and adding it to the
pipeline in `tracker.py`. No other module needs to change.
