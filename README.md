# Token Trace

A local, periodic tracker for your **AI tool token usage** (GitHub Copilot CLI
and Claude Code CLI). It records activity at a **daily grain** (per source, per
model, per repo/workspace) so you can roll it up later **by day, month, or year**.

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
| Copilot CLI | `~/.copilot/session-store.db` + `session-state/<id>/events.jsonl` + `logs/process-*.log` | sessions, turns, repo/branch, per-model token counts, context-window peak |
| Claude Code CLI | `~/.claude/projects/**/*.jsonl` | per-model token counts (input, output, cache read/write) |

## Requirements

- Python 3.10+ (standard library only at runtime).
- `pytest` only for running the tests: `pip install -r requirements.txt`.

## Usage

```bash
# Collect (the scheduled job). Re-scans the last N days and upserts.
python tracker.py collect                 # default lookback: 3 days
python tracker.py collect --lookback 30   # backfill more history

# Report — roll up by day / month / year
python tracker.py report --period day
python tracker.py report --period month
python tracker.py report --period year

# Filter and/or emit JSON
python tracker.py report --period month --source copilot-cli --model claude-sonnet-4
python tracker.py report --period year --json
```

The database lives at `usage.db` next to `tracker.py` by default (override
with `--db`). Re-running `collect` is **idempotent** — each day is recomputed
from the cumulative source files and overwritten.

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

Register-ScheduledTask -TaskName "AI Usage Tracker" `
  -Action $action -Trigger $trigger -Settings $settings
```

To remove it: `Unregister-ScheduledTask -TaskName "AI Usage Tracker"`.

**macOS (launchd):** create a plist in `~/Library/LaunchAgents/` that runs
`python3 /path/to/tracker.py collect` on an hourly interval.

## Project layout

```
ai-token/
├─ tracker.py                # CLI entry (collect / report)
├─ src/
│  ├─ models.py             # ActivityRecord (frozen dataclass) + merge
│  ├─ collectors/           # one collector per AI tool surface
│  │  ├─ base.py            # ActivityCollector protocol + time helpers
│  │  ├─ copilot_cli.py     # Copilot CLI token + activity collector
│  │  └─ claude_cli.py      # Claude Code CLI token collector
│  ├─ store.py              # UsageStore (sqlite, idempotent upsert)
│  ├─ report.py             # UsageReporter (day/month/year roll-ups)
│  ├─ pipeline.py           # fluent TrackerPipeline
│  └─ config.py             # paths, lookback, db location
├─ tests/                   # pytest suite with fixtures
└─ docs/                    # design docs and implementation plans
```

## Development

```bash
pip install -r requirements.txt
python -m pytest -q
```

## Extending

Add a new surface by implementing the `ActivityCollector` protocol
(`collect(since) -> Iterable[ActivityRecord]`) and adding it to the pipeline in
`tracker.py`. No other module needs to change.
