<h1 align="center">Token Trace</h1>

<p align="center">
  A local, periodic tracker for your AI tool token usage — session-grain records from GitHub Copilot CLI and Claude Code CLI, stored in SQLite, rolled up however you want.
</p>

<p align="center">
  <a href="https://github.com/rbipin/TokenTrace/actions/workflows/ci.yml"><img src="https://github.com/rbipin/TokenTrace/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://github.com/rbipin/TokenTrace/actions/workflows/release.yml"><img src="https://github.com/rbipin/TokenTrace/actions/workflows/release.yml/badge.svg" alt="Release" /></a>
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/SQLite-local--first-003B57?logo=sqlite" />
  <img src="https://img.shields.io/badge/runtime%20deps-stdlib%20only-brightgreen" />
</p>

<br />

<img width="1536" height="800" alt="TokenTrace" src="https://github.com/user-attachments/assets/25f04f4b-ecc8-4b9f-95be-52098c9bffed" />

---

## Overview

<!-- description -->
Token Trace records AI tool activity at a **session grain** (one row per
session per model) so you can roll it up by day, month, or year — and drill
into individual sessions or projects.
<!-- /description -->

<!-- purpose -->
I use AI tools heavily from the CLI and noticed that none of the existing AI harnesses — Copilot, Claude Code, etc. — give a meaningful token-level breakdown or trend over time. They show activity in the moment but offer no persistent view of how much you're consuming or how efficiently you're using it. No dashboard exists today that tracks and reports this across tools in one place.

This project is my answer to that gap: a lightweight local collector that pulls token data from the sources that actually persist it to disk, stores it in SQLite, and lets me query it however I want. The goal is to understand my AI token usage and how it trends over time — and eventually use the data for things like a heatmap of usage intensity across days or models.
<!-- /purpose -->

**Built entirely with Claude Code** — requirements, direction, and corrections were provided by me; implementation was handled by Claude.

> **Why only CLI surfaces?**
>
> These are the only surfaces that write **actual token counts** to disk.
> Copilot CLI records them via the `session.shutdown` event's `modelMetrics`.
> Claude Code CLI records them in per-conversation JSONL files under
> `~/.claude/projects/`. VS Code and web UIs render token data live but do not
> persist it locally. See `docs/plans/2026-06-15-copilot-usage-tracker-design.md`
> for the original rationale.

---

## Outcome

<!-- outcome -->
This usage analytics tool provides exact token counts per session, model, and tool, along with cache efficiency metrics and estimated cost savings. Key features include:

- Tracks tool calls per session (Copilot tool events / Claude tool_use blocks)
- Tracks context peak — the largest single-request token footprint per session (main conversation only; models used solely by subagents show 0)
- Trend analysis across daily, monthly, and yearly views
- Optional project-level usage breakdowns and context labels (work / personal)
- Idempotent data collection for safe scheduled runs
- Remote synchronization to pluggable backends like Supabase
- Heatmap-ready datasets for future visualization and usage-intensity analysis
<!-- /outcome -->

---

## Tech Stack

<!-- techstack -->
| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Storage | SQLite |
| Config | TOML, .env |
| Testing | pytest |

<!-- /techstack -->

---

## Data Sources

| Source | Location | Metrics |
|---|---|---|
| Copilot CLI | `~/.copilot/session-store.db` + `session-state/<id>/events.jsonl` | sessions, turns, per-model token counts (input, output, cache read/write, reasoning), tool calls, context peak tokens |
| Claude Code CLI | `~/.claude/projects/**/*.jsonl` | per-session token counts (input, output, cache read/write), tool calls, context peak tokens |

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ — standard library only at runtime (`tomllib` for config) |
| Storage | SQLite — `~/.tokentracer/usage.db`, idempotent upserts, per-store sync tracking |
| Remote stores | Pluggable `SessionStore` protocol via the `tokentracer.stores` entry-point group; Supabase built in (optional extra) |
| Config | TOML — `~/.tokentracer.toml`, secrets via env vars or `~/.tokentracer.env` |
| Packaging | `pyproject.toml` console script `tokentracer` — installable with pipx / uv / pip |
| Testing | pytest (`pip install -r requirements.txt`) |

---

## Architecture

The tracker follows an **Open/Closed pipeline**: adding a new data source only requires implementing the `ActivityCollector` protocol and registering it in `tracker.py`. No other module needs to change.

```
Collector.collect(since)          one collector per AI tool surface
  └── List[SessionRecord]         frozen dataclass, one per (session, model)
        └── merge_records         deduplicates by (session_id, source, model)
              └── SqliteStore.upsert   last-write-wins, idempotent
                    └── UsageReporter  day / month / year / all roll-ups
                    └── tokentracer sync → remote stores (e.g. Supabase)
```

**Key invariants:**

- `collect` is always **idempotent** — re-running overwrites existing session rows. Merge key is `(session_id, source, model)`.
- Upsert is **last-write-wins** — no summation across runs.
- Collectors are **read-only** with respect to their source files.

Before records are written, they pass through a pluggable **middleware**
pipeline. Today that's model-name normalization: raw model identifiers
reported differently by each source (e.g. Copilot vs. Claude Code) are
resolved to a stable `canonical_model`, so reports can group and filter by
model regardless of which tool collected the session. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#middleware) for how the
middleware system works and how to add a new one.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture: collect data flow, exact source files and schemas, storage, sync, and extension points. [docs/DESIGN-HISTORY.md](docs/DESIGN-HISTORY.md) records the design decisions that shaped the project.

---

## Project Structure

```
ai-token/
├─ pyproject.toml            # packaging — pip/pipx/uv entry point
├─ tracker.py                # CLI entry (collect / report / config / sync / projects)
├─ src/
│  ├─ models.py             # SessionRecord (frozen dataclass) + merge
│  ├─ project_identity.py   # Tri-state project naming + local-only cwd→guid→whimsical identities
│  ├─ collectors/           # one collector per AI tool surface
│  │  ├─ base.py            # ActivityCollector protocol + to_date / to_local_iso helpers
│  │  ├─ copilot_cli.py     # Copilot CLI — one record per (session, model)
│  │  └─ claude_cli.py      # Claude Code CLI — one record per JSONL session
│  ├─ whimsy/               # Standalone docker-style masked-name generator (extractable, stdlib-only)
│  ├─ stores/               # pluggable store backends (entry-point registry)
│  │  ├─ __init__.py        # SessionStore protocol
│  │  ├─ registry.py        # store discovery + instantiation (env var expansion)
│  │  ├─ sqlite.py          # SqliteStore — local db, idempotent upsert, sync tracking
│  │  └─ supabase.py        # SupabaseStore — remote Supabase sink
│  ├─ report.py             # UsageReporter (day/month/year, cache efficiency, --summary, --by-project, --detailed)
│  ├─ pipeline.py           # fluent TrackerPipeline
│  └─ config.py             # Paths, TOML loading, write_toml_setting
├─ tests/                   # pytest suite with fixtures
└─ docs/                    # design docs and implementation plans
```

---

## Getting Started

### Prerequisites

- Python 3.11+ (standard library only at runtime)
- `pytest` only for running the tests: `pip install -r requirements.txt`

### Install

The **base package** covers local collection and reporting (`collect` /
`report` / `config` — standard library only). If you want to push records to
a **remote store** (e.g. Supabase via `tokentracer sync`), install with the
store's extra — e.g. `tokentracer[supabase]` — otherwise `collect`/`sync`
will warn that the store's client library is missing.

**From a GitHub Release** (replace `0.1.0` with the latest version):

```bash
# uv
uv tool install https://github.com/rbipin/TokenTrace/releases/download/v0.1.0/tokentracer-0.1.0-py3-none-any.whl

# pip
pip install https://github.com/rbipin/TokenTrace/releases/download/v0.1.0/tokentracer-0.1.0-py3-none-any.whl

# from source at a tag
uv tool install git+https://github.com/rbipin/TokenTrace@{version}
pip install git+https://github.com/rbipin/TokenTrace@v0.1.0

# with the Supabase store extra
uv tool install "tokentracer[supabase] @ git+https://github.com/rbipin/TokenTrace@v0.1.0"
pip install "tokentracer[supabase] @ git+https://github.com/rbipin/TokenTrace@v0.1.0"
```

To add an extra to an existing uv tool install, re-run with `--force`:

```bash
uv tool install --force "tokentracer[supabase] @ git+https://github.com/rbipin/TokenTrace@v0.1.0"
```

**From the latest main branch:**

```bash
# pipx (recommended)
pipx install git+https://github.com/rbipin/TokenTrace

# uv
uv tool install git+https://github.com/rbipin/TokenTrace
```

**From source (clone locally):**

```bash
git clone https://github.com/rbipin/TokenTrace
pipx install .          # or: uv tool install .
# with the Supabase store: pipx install ".[supabase]"  /  uv tool install ".[supabase]"
```

### First run

```bash
tokentracer collect
tokentracer report
```

The database is created at `~/.tokentracer/usage.db` on first run. Override with `--db`.

---

## Usage

```bash
# Collect (the scheduled job). Re-scans the last N days and upserts.
python3 tracker.py collect                      # default lookback: 3 days
python3 tracker.py collect --lookback 90        # backfill more history
python tracker.py collect --project-mode whimsical
# project modes: yes = real name, no = stable 12-hex guid per cwd, whimsical = masked docker-style name

# Default report — today's sessions, one row per session, full token detail
python3 tracker.py report
# Columns: Project  Source  Model  Start  End  Input  Output  Reasoning  CacheRead  CacheCreate  CacheHit%  CtxPeak  Turns  Tools

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

# Dump every row in the db with all columns and sync status
python3 tracker.py report --detailed

# Filter by model, emit JSON — combinable with any of the above
python3 tracker.py report --period month --model claude-sonnet-4-6
python3 tracker.py report --summary --period all --by-project --json

# List local project identities (cwd -> guid -> whimsical name; never synced)
python3 tracker.py projects

# Sync unsynced records to configured remote stores (e.g., Supabase)
python3 tracker.py sync
python3 tracker.py sync --dry-run   # show pending counts without pushing

# Configuration (persisted to ~/.tokentracer.toml)
python tracker.py config set track_project_names whimsical
python3 tracker.py config set context work        # label this machine's usage as "work"
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

---

## Configuration

Settings are stored in `~/.tokentracer.toml` in your home directory:

| OS | Config file | Env file (secrets) |
| --- | --- | --- |
| macOS / Linux | `~/.tokentracer.toml` | `~/.tokentracer.env` |
| Windows | `C:\Users\<you>\.tokentracer.toml` | `C:\Users\<you>\.tokentracer.env` |

You can edit the file directly
or use `tracker.py config set <key> <value>` to update individual keys.

```toml
# ~/.tokentracer.toml

[tracking]
# Project labeling mode stored in sessions.project.
# "yes" = real project/repo name.
# "no" = stable 12-hex guid per cwd (default).
# "whimsical" = stable docker-style masked name like admiring_agnesi.
# Override per-run with: --project-mode {yes,no,whimsical}
track_project_names = "no"

# Usage context label stamped on every collected session record and stored
# in the database (local SQLite and remote stores). Use it to differentiate
# work from personal usage, e.g. set "work" on your work machine.
# Default: "personal". Override per-run with: --context <label>
context = "personal"
```

Set a value from the CLI (rewrites the file safely, preserving other keys):

```bash
python tracker.py config set track_project_names whimsical
python3 tracker.py config set context work
```

`tracker.py config set track_project_names` validates the enum and rejects old
boolean values such as `true` / `false`. If `~/.tokentracer.toml` still contains
a legacy boolean or any other invalid value, TokenTrace warns and falls back to
`"no"`.

Masked modes keep a local-only `project_identities` table inside `usage.db`
(`cwd_key` case-insensitive → `guid` → `whimsical_name`). Sync never uploads
that table, so remote stores only ever see the already-resolved value stored in
`sessions.project`.

CLI flags `--project-mode <yes|no|whimsical>` and `--context <label>` on the
`collect` subcommand override the file values for that run only.

---

## Remote Stores (sync)

Beyond the local SQLite database, TokenTracer can push session records to
**remote stores** via a pluggable stores registry. Each store implements the
`SessionStore` protocol (`name`, `upsert(records)`, `close()`), and remote
stores are configured with `[stores.<name>]` sections in `~/.tokentracer.toml`. `${VAR}` placeholders in
values are resolved from environment variables first, then from a
`~/.tokentracer.env` file (`KEY=VALUE` lines), so secrets never live in the
config file.

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
  tool_calls bigint default 0,
  input_tokens bigint default 0,
  output_tokens bigint default 0,
  cache_creation_tokens bigint default 0,
  cache_read_tokens bigint default 0,
  reasoning_tokens bigint default 0,
  context_peak_tokens bigint default 0,
  context text default 'personal',
  primary key (session_id, source, model)
);
```

If you have an existing `token_sessions` table, add the new columns:

```sql
ALTER TABLE token_sessions ADD COLUMN tool_calls bigint DEFAULT 0;
ALTER TABLE token_sessions ADD COLUMN reasoning_tokens bigint DEFAULT 0;
ALTER TABLE token_sessions ADD COLUMN context_peak_tokens bigint DEFAULT 0;
```

Then:

```bash
# Either set environment variables:
export SUPABASE_URL=https://<project>.supabase.co
export SUPABASE_KEY=<service-role-key>

# ...or put them in ~/.tokentracer.env (env vars win if both are set):
#   SUPABASE_URL=https://<project>.supabase.co
#   SUPABASE_KEY=<service-role-key>

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

---

## Run It Periodically

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

---

## Development

```bash
pip install -r requirements.txt
python3 -m pytest -q
```

### Extending

Add a new **surface** by implementing the `ActivityCollector` protocol
(`collect(since: date) -> Iterable[SessionRecord]`) and adding it to the
pipeline in `tracker.py`. Add a new **store backend** by implementing the
`SessionStore` protocol and registering it via the `tokentracer.stores`
entry-point group (see [Writing your own store](#writing-your-own-store)).
No other module needs to change.

### Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and commit to `main`.
2. `git tag v<version> && git push origin v<version>`.
3. CI tests, verifies the tag matches the version, builds, and publishes the GitHub Release automatically.

---

## License

Licensed under the [PolyForm Shield License 1.0.0](LICENSE.md) — free to
use, modify, and distribute for any purpose except providing a product or
service that competes with this software.

Required Notice: Copyright Bipin Radhakrishnan (https://github.com/rbipin/TokenTrace)
