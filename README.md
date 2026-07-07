<img width="1536" height="800" alt="TokenTrace" src="https://github.com/user-attachments/assets/25f04f4b-ecc8-4b9f-95be-52098c9bffed" />

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

## Installation

**pipx (recommended):**
```bash
pipx install tokentracer
tokentracer collect
tokentracer report
```

**uv:**
```bash
uv tool install tokentracer
tokentracer collect
tokentracer report
```

**From source:**
```bash
git clone https://github.com/rbipin/TokenTracer
pipx install .          # or: uv tool install .
```

The database is created at `~/.tokentracer/usage.db` on first run. Override with `--db`.

## Usage

```bash
# Collect (the scheduled job). Re-scans the last N days and upserts.
python3 tracker.py collect                      # default lookback: 3 days
python3 tracker.py collect --lookback 90        # backfill more history
python3 tracker.py collect --track-projects     # store project names this run
python3 tracker.py collect --no-track-projects  # suppress project names this run

# Default report — today's sessions, one row per session, full token detail
python3 tracker.py report
# Columns: Project  Source  Model  Start  End  Input  Output  CacheRead  CacheCreate  CacheHit%  Turns

# Scope to a different period (all | day | month | year)
python3 tracker.py report --period month        # this month's sessions, detailed
python3 tracker.py report --period all          # every session in the database

# --summary: compact per-session view (Session Project Date Start End Turns Tokens CacheHit%)
python3 tracker.py report --summary

# --summary + period: aggregated roll-up grouped by period+model
python3 tracker.py report --summary --period month
python3 tracker.py report --summary --period year
python3 tracker.py report --summary --period all

# --by-project: group by project
python3 tracker.py report --by-project                          # today, by project
python3 tracker.py report --summary --period all --by-project   # all history, by project

# Filter by model, emit JSON — combinable with any of the above
python3 tracker.py report --period month --model claude-sonnet-4-6
python3 tracker.py report --summary --period all --by-project --json

# Sync unsynced records to configured remote stores (e.g., Supabase)
python3 tracker.py sync
python3 tracker.py sync --dry-run   # show pending counts without pushing

# Configuration (persisted to ~/.tokentracer.toml)
python3 tracker.py config set track_project_names true
```

The database lives at `~/.tokentracer/usage.db` by default (override with
`--db`). Re-running `collect` is **idempotent** — each session is identified
by its unique ID and re-collecting overwrites the stored row.

### Cache efficiency

Every text report opens with a cache efficiency summary:

```
Cache efficiency: 72% read from cache (~65% cost saved)
```

This is computed across all sessions in the database and shows what fraction of
your total token budget came from the cache, and the approximate cost saving
(cache reads cost ~10% of regular input tokens).

## Configuration

Settings are stored in `~/.tokentracer.toml`. You can edit the file directly
or use `tracker.py config set <key> <value>` to update individual keys.

```toml
# ~/.tokentracer.toml

[tracking]
# Store the project/repo name on each session record.
# Derived from the repository field (Copilot) or the cwd (Claude Code).
# Off by default — enable if you want per-project breakdowns.
# Override per-run with: --track-projects / --no-track-projects
track_project_names = false
```

Set a value from the CLI (rewrites the file safely, preserving other keys):

```bash
python3 tracker.py config set track_project_names true
```

CLI flags `--track-projects` and `--no-track-projects` on the `collect`
subcommand override the file value for that run only.

## Remote stores (sync)

Beyond the local SQLite database, TokenTracer can push session records to
**remote stores** via a pluggable stores registry. Each store implements the
`SessionStore` protocol (`name`, `upsert(records)`, `close()`), and remote
stores are configured with `[stores.<name>]` sections in `~/.tokentracer.toml`.
`${VAR}` placeholders in values are expanded from environment variables, so
secrets never live in the config file.

Run `tokentracer sync` to push records that haven't been synced to each store
yet (sync state is tracked per store in the local database, so re-running is
cheap and idempotent). Use `--dry-run` to see pending counts first.

### Supabase (built in)

A Supabase store ships with TokenTracer. Install the optional dependency and
configure it:

```bash
pip install "tokentracer[supabase]"     # pulls in supabase-py >= 2.0
```

```toml
# ~/.tokentracer.toml
[stores.supabase]
url = "${SUPABASE_URL}"
key = "${SUPABASE_KEY}"      # service role key
table = "token_sessions"     # optional, this is the default
```

Create the table once in the Supabase SQL editor:

```sql
create table token_sessions (
  session_id text not null,
  source text not null,
  model text not null,
  date date,
  start_ts timestamptz,
  end_ts timestamptz,
  project text,
  turns integer default 0,
  input_tokens bigint default 0,
  output_tokens bigint default 0,
  cache_creation_tokens bigint default 0,
  cache_read_tokens bigint default 0,
  primary key (session_id, source, model)
);
```

Then:

```bash
export SUPABASE_URL=https://<project>.supabase.co
export SUPABASE_KEY=<service-role-key>
tokentracer sync --dry-run   # preview
tokentracer sync             # push
```

Rows are upserted with conflict key `(session_id, source, model)` — the same
primary key as the local database — so syncing is idempotent too.

### Writing your own store

Stores are discovered through the `tokentracer.stores` entry-point group, so
you can add a new backend (Postgres, S3, an HTTP API, …) without touching
TokenTracer's code:

1. Implement the protocol in your own package:

   ```python
   from src.stores import SessionStore  # Protocol: name, upsert, close

   class MyStore:
       name = "mystore"
       def __init__(self, url: str): ...
       def upsert(self, records) -> int: ...
       def close(self) -> None: ...
   ```

2. Declare the entry point in your package's `pyproject.toml`:

   ```toml
   [project.entry-points."tokentracer.stores"]
   mystore = "my_package.store:MyStore"
   ```

3. Enable it in `~/.tokentracer.toml` — constructor kwargs come straight from
   the section (with `${VAR}` env expansion):

   ```toml
   [stores.mystore]
   url = "${MYSTORE_URL}"
   ```

Alternatively, skip packaging and point directly at a class with
`class = "my_module.MyStore"` in the store's config section.


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
├─ pyproject.toml            # packaging — pip/pipx/uv entry point
├─ tracker.py                # CLI entry (collect / report / config)
├─ src/
│  ├─ models.py             # SessionRecord (frozen dataclass) + merge
│  ├─ collectors/           # one collector per AI tool surface
│  │  ├─ base.py            # ActivityCollector protocol + to_date / to_local_iso helpers
│  │  ├─ copilot_cli.py     # Copilot CLI — one record per (session, model)
│  │  └─ claude_cli.py      # Claude Code CLI — one record per JSONL session
│  ├─ stores/               # pluggable store backends (entry-point registry)
│  │  ├─ __init__.py        # SessionStore protocol
│  │  ├─ registry.py        # store discovery + instantiation (env var expansion)
│  │  ├─ sqlite.py          # SqliteStore — local db, idempotent upsert, sync tracking
│  │  └─ supabase.py        # SupabaseStore — remote Supabase sink
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

Add a new **surface** by implementing the `ActivityCollector` protocol
(`collect(since: date) -> Iterable[SessionRecord]`) and adding it to the
pipeline in `tracker.py`. Add a new **store backend** by implementing the
`SessionStore` protocol and registering it via the `tokentracer.stores`
entry-point group (see [Writing your own store](#writing-your-own-store)).
No other module needs to change.
