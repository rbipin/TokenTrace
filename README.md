# Copilot Usage Tracker

A local, periodic tracker for your **GitHub Copilot CLI** token usage on
Windows. It records activity at a **daily grain** (per model, per repo/workspace)
so you can roll it up later **by day, month, or year**.

> **Why Copilot CLI only**
>
> The Copilot CLI is the only surface that writes **actual token counts** to
> disk (input, output, cache read/write, reasoning) via the
> `session.shutdown` event's `modelMetrics`. VS Code and Visual Studio Copilot
> Chat render token/cost data only live in their Chat Debug Views and do **not**
> persist it — so they cannot be tracked locally. This tool therefore focuses on
> the CLI, where the data is real and complete, and also records the CLI's
> **context-window peak** (max tokens held in the model's context window).
> See `docs/plans/2026-06-15-copilot-usage-tracker-design.md` for the full
> rationale.

## Data source

| Source | Location | Metrics |
|---|---|---|
| Copilot CLI | `%USERPROFILE%\.copilot\session-store.db` + `session-state\<id>\events.jsonl` + `logs\process-*.log` | sessions, turns, repo/branch, per-model token counts, context-window peak |

## Requirements

- Python 3.10+ (standard library only at runtime).
- `pytest` only for running the tests: `pip install -r requirements.txt`.

## Usage

```powershell
# Collect (the scheduled job). Re-scans the last N days and upserts.
python tracker.py collect                 # default lookback: 3 days
python tracker.py collect --lookback 30   # backfill more history

# Report — roll up by day / month / year
python tracker.py report --period day
python tracker.py report --period month
python tracker.py report --period year

# Filter and/or emit JSON (for a future dashboard)
python tracker.py report --period month --source copilot-cli --model claude-sonnet-4
python tracker.py report --period year --json
```

The database lives at `%LOCALAPPDATA%\ai-token\usage.db` by default (override
with `--db`). Re-running `collect` is **idempotent** — each day is recomputed
from the cumulative source files and overwritten.

## Run it periodically (Windows Task Scheduler)

Create an hourly task that runs the collector. Adjust the Python path and repo
path as needed:

```powershell
$python = (Get-Command python).Source
$script = "C:\Repo\me\ai-token\tracker.py"

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" collect"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
             -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName "Copilot Usage Tracker" `
  -Action $action -Trigger $trigger -Settings $settings `
  -Description "Collects local GitHub Copilot activity hourly"
```

To remove it later: `Unregister-ScheduledTask -TaskName "Copilot Usage Tracker"`.

## Project layout

```
ai-token/
├─ tracker.py                # CLI entry (collect / report)
├─ aitoken/
│  ├─ models.py             # ActivityRecord (frozen dataclass) + merge
│  ├─ collectors/           # one collector per Copilot surface
│  │  ├─ base.py            # ActivityCollector protocol + time helpers
│  │  └─ copilot_cli.py     # Copilot CLI token + activity collector
│  ├─ store.py              # UsageStore (sqlite, idempotent upsert)
│  ├─ report.py             # UsageReporter (day/month/year roll-ups)
│  ├─ pipeline.py           # fluent TrackerPipeline
│  └─ config.py             # paths, lookback, db location
├─ tests/                   # pytest suite with fixtures
└─ docs/plans/              # design document
```

## Development

```powershell
pip install -r requirements.txt
python -m pytest -q
```

## Extending

Add a new surface by implementing the `ActivityCollector` protocol
(`collect(since) -> Iterable[ActivityRecord]`) and adding it to the pipeline in
`tracker.py`. No other module needs to change (Open/Closed).
