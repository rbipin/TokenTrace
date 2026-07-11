# Collect/Sync Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `collect` correctly track which records reached remote stores, and automatically retry anything still unsynced (including leftovers from earlier failed runs), without adding a second scheduled task.

**Architecture:** `TrackerPipeline.run()` already pushes fresh records to remote stores inline; it just needs to record success in `sync_log` via `SqliteStore.mark_synced()`. `CollectCommand.run()` then reuses the same store instances to sweep whatever `sync_log` still shows unsynced (via the sync command's existing retry logic, relocated to a shared helper). `register-task.ps1` gets output redirection so Windows scheduled runs actually produce a log file, matching what `register-task.sh` already does via `StandardOutPath`/`StandardErrorPath`.

**Tech Stack:** Python 3, stdlib only (`sqlite3`, `argparse`), `pytest` for tests. No new dependencies.

## Global Constraints

- Standard library only at runtime — no new third-party dependencies (per CLAUDE.md).
- `src/pipeline.py` must not hard-import `SqliteStore` — remote-store detection of `mark_synced` support stays duck-typed (`hasattr`), consistent with the existing `SessionStore` Protocol pattern in `src/stores/__init__.py`.
- `collect` must remain idempotent and must not change the registered scheduled-task cadence (`register-task.sh` / `register-task.ps1` keep calling `collect` only, once daily).
- No SQLite schema/migration changes — `sessions` and `sync_log` tables are unchanged.

---

### Task 1: Move `_run_sync` into `src/commands/common.py` as public `run_sync`

**Files:**
- Modify: `src/commands/common.py` (add `run_sync`)
- Modify: `src/commands/sync.py:1-43` (remove `_run_sync` definition, import and call `run_sync` from `common.py`)
- Test: `tests/test_sync_command.py`

**Interfaces:**
- Produces: `run_sync(sqlite_store, remote_stores: list, dry_run: bool) -> dict` in `src/commands/common.py`. Same signature and return shape as the old `_run_sync` (`{store_name: {"pushed": N, "failed": bool} | {"pending": N}}`), logic unchanged — only the location and name change.

- [ ] **Step 1: Update the test file to import from the new location (this will fail first)**

Edit `tests/test_sync_command.py`: replace every `from src.commands.sync import _run_sync` with `from src.commands.common import run_sync`, and every call `_run_sync(...)` with `run_sync(...)`. There are 4 occurrences (one per test function). Resulting file:

```python
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
import pytest
from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


class _StubRemote:
    def __init__(self, name, boom=False):
        self.name = name
        self._boom = boom
        self.pushed: list[SessionRecord] = []

    def upsert(self, records):
        if self._boom:
            raise RuntimeError("connection refused")
        self.pushed.extend(records)
        return len(records)

    def close(self):
        pass


def _rec(sid):
    return SessionRecord(session_id=sid, source="claude_cli",
                         model="claude-sonnet-4-6", date="2026-07-01", turns=1)


def _seed(db: Path, *session_ids):
    store = SqliteStore(db)
    store.upsert([_rec(sid) for sid in session_ids])
    return store


def test_sync_pushes_unsynced(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1", "s2")
    remote = _StubRemote("supabase")

    from src.commands.common import run_sync
    result = run_sync(sqlite, [remote], dry_run=False)

    assert result == {"supabase": {"pushed": 2, "failed": False}}
    assert len(remote.pushed) == 2
    assert sqlite.unsynced_for("supabase") == []


def test_sync_dry_run_does_not_push(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1")
    remote = _StubRemote("supabase")

    from src.commands.common import run_sync
    result = run_sync(sqlite, [remote], dry_run=True)

    assert result == {"supabase": {"pending": 1}}
    assert remote.pushed == []
    assert len(sqlite.unsynced_for("supabase")) == 1


def test_sync_remote_failure_leaves_unsynced(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1")
    bad_remote = _StubRemote("cosmos", boom=True)

    from src.commands.common import run_sync
    result = run_sync(sqlite, [bad_remote], dry_run=False)

    assert result["cosmos"]["failed"] is True
    assert len(sqlite.unsynced_for("cosmos")) == 1


def test_sync_already_synced_not_pushed_again(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1", "s2")
    remote = _StubRemote("supabase")
    # Pre-mark s1 as synced
    sqlite.mark_synced([_rec("s1")], "supabase")

    from src.commands.common import run_sync
    run_sync(sqlite, [remote], dry_run=False)

    assert len(remote.pushed) == 1
    assert remote.pushed[0].session_id == "s2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sync_command.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_sync' from 'src.commands.common'` (4 errors)

- [ ] **Step 3: Move the function into `common.py`**

Edit `src/commands/common.py` to add `run_sync`, unchanged logic from the old `_run_sync`:

```python
"""Helpers shared by multiple commands."""
from __future__ import annotations

import sys

from src.config import Config
from src.stores.registry import instantiate_store


def load_remote_stores(cfg: Config) -> list:
    """Instantiate configured remote stores, warning on failures."""
    stores = []
    for sc in cfg.remote_stores:
        try:
            stores.append(instantiate_store(sc.name, sc.params, sc.class_path))
        except Exception as exc:
            print(f"Warning: could not load store {sc.name!r}: {exc}", file=sys.stderr)
    return stores


def run_sync(
    sqlite_store,
    remote_stores: list,
    dry_run: bool,
) -> dict:
    """Push unsynced records from sqlite_store to each remote store.

    Returns a dict: {store_name: {"pushed": N, "failed": bool} | {"pending": N}}
    """
    result = {}
    for store in remote_stores:
        pending = sqlite_store.unsynced_for(store.name)
        if dry_run:
            result[store.name] = {"pending": len(pending)}
            store.close()
            continue
        try:
            if pending:
                store.upsert(pending)
                sqlite_store.mark_synced(pending, store.name)
            result[store.name] = {"pushed": len(pending), "failed": False}
        except Exception as exc:
            print(f"Warning [{store.name}]: {exc}", file=sys.stderr)
            result[store.name] = {"pushed": 0, "failed": True, "error": str(exc)}
        finally:
            try:
                store.close()
            except Exception:
                pass
    return result
```

- [ ] **Step 4: Update `sync.py` to use the relocated helper**

Edit `src/commands/sync.py`: remove the `_run_sync` function definition (lines 13-42) and the unused `from src.commands.common import load_remote_stores` stays, but add `run_sync` to that import; update the call site in `SyncCommand.run()`. Resulting file:

```python
"""The `sync` subcommand: push unsynced records to remote stores."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.commands.common import load_remote_stores, run_sync
from src.config import Config
from src.stores.sqlite import SqliteStore


class SyncCommand:
    name = "sync"
    help = "push unsynced records to remote stores"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--dry-run", action="store_true",
                            help="show pending counts without pushing")

    def run(self, args: argparse.Namespace) -> int:
        cfg = Config.load()
        db_path = Path(args.db) if args.db else cfg.db_path

        if not cfg.remote_stores:
            print("No remote stores configured. Add [stores.X] sections to "
                  "~/.tokentracer/.tokentracer.toml")
            return 0

        sqlite_store = SqliteStore(db_path)
        remote_stores = load_remote_stores(cfg)

        if not remote_stores:
            print("No remote stores could be loaded.")
            return 1

        label = "(dry run) " if args.dry_run else ""
        print(f"Syncing {len(remote_stores)} store(s)... {label}")
        result = run_sync(sqlite_store, remote_stores, dry_run=args.dry_run)

        for store_name, info in result.items():
            if args.dry_run:
                print(f"  {store_name:<12} {info['pending']} pending")
            elif info["failed"]:
                unsynced = len(sqlite_store.unsynced_for(store_name))
                print(f"  {store_name:<12} failed ({unsynced} records pending)")
            else:
                print(f"  {store_name:<12} {info['pushed']} records pushed")

        return 0
```

Note: `sys` import is dropped from `sync.py` since it's no longer used there directly (only `common.py` needs it now).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sync_command.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Run the full test suite to check nothing else references the old name**

Run: `python3 -m pytest -q`
Expected: all pass (no other file imports `_run_sync` from `src.commands.sync`)

- [ ] **Step 7: Commit**

```bash
git add src/commands/common.py src/commands/sync.py tests/test_sync_command.py
git commit -m "refactor: relocate sync-push logic to commands/common as run_sync"
```

---

### Task 2: Mark remote pushes as synced inside `TrackerPipeline.run()`

**Files:**
- Modify: `src/pipeline.py:110-125`
- Test: `tests/test_pipeline_multi_store.py`

**Interfaces:**
- Consumes: `SqliteStore.mark_synced(records: list[SessionRecord], store_name: str) -> None` (already exists, `src/stores/sqlite.py:185-197`). Detected via `hasattr(self._stores[0], "mark_synced")` — no import of `SqliteStore` in `pipeline.py`.
- Produces: after `TrackerPipeline.run()`, any remote store whose `upsert()` succeeded is marked synced in `stores[0]`'s `sync_log` (when `stores[0]` supports `mark_synced`); a remote store whose `upsert()` raised leaves its records unsynced, unchanged from today's behavior.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_multi_store.py`:

```python
def test_successful_remote_push_marks_synced(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    remote = _StubStore("remote_a")
    col = _StubCollector("claude_cli", [_rec("s1")])
    (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, remote)
        .run()
    )
    assert sqlite.unsynced_for("remote_a") == []


def test_failed_remote_push_leaves_unsynced(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    bad_remote = _StubStore("remote_bad", boom=True)
    col = _StubCollector("claude_cli", [_rec("s1")])
    (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, bad_remote)
        .run()
    )
    assert len(sqlite.unsynced_for("remote_bad")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_pipeline_multi_store.py -v -k "marks_synced or leaves_unsynced"`
Expected: FAIL — `test_successful_remote_push_marks_synced` fails because `sqlite.unsynced_for("remote_a")` still returns the record (nothing marks it synced today). `test_failed_remote_push_leaves_unsynced` should already pass (no behavior change needed for the failure path) — confirms it's not a false positive.

- [ ] **Step 3: Implement `mark_synced` call in the remote push path**

Edit `src/pipeline.py`, the `_push` closure inside `run()` (currently lines 113-119):

```python
        def _push(store: SessionStore) -> str | None:
            try:
                store.upsert(merged)
                store.close()
                if hasattr(self._stores[0], "mark_synced"):
                    self._stores[0].mark_synced(merged, store.name)
                return None
            except Exception as exc:
                return f"{store.name}: {exc}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_pipeline_multi_store.py -v`
Expected: PASS (5 passed — 3 existing + 2 new)

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/pipeline.py tests/test_pipeline_multi_store.py
git commit -m "fix: mark remote pushes as synced during collect's inline push"
```

---

### Task 3: `collect` sweeps unsynced records after the inline push

**Files:**
- Modify: `src/commands/collect.py`
- Test: `tests/test_tracker_cli.py`

**Interfaces:**
- Consumes: `run_sync(sqlite_store, remote_stores: list, dry_run: bool) -> dict` from `src/commands/common.py` (Task 1). `_build_stores(cfg) -> list` (`src/commands/collect.py`, unchanged — `stores[0]` is always the local `SqliteStore`-compatible store, `stores[1:]` are remotes).
- Produces: `CollectCommand.run()` prints `Synced N pending record(s) to <store>` to stdout for each remote store that had pending records pushed by the post-collect sweep. Prints nothing when there is nothing pending. Sweep failures print via `run_sync`'s own `Warning [<store>]: <error>` to stderr (already covered by Task 1 — no new failure-printing code needed here).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tracker_cli.py`:

```python
def test_cmd_collect_sweeps_unsynced_records(tmp_path, monkeypatch, capsys):
    class FakeResult:
        errors = ()
        stores_failed = ()
        records_written = 0
        collectors_run = 0

    class FakePipeline:
        def since(self, _):
            return self

        def stores(self, *_):
            return self

        def run(self):
            return FakeResult()

    class FakeSqlite:
        def __init__(self, pending):
            self._pending = list(pending)
            self.marked = []

        def unsynced_for(self, name):
            return list(self._pending)

        def mark_synced(self, records, name):
            self.marked.append((name, [r.session_id for r in records]))
            self._pending = []

        def close(self):
            pass

    class FakeRemote:
        name = "remote_a"

        def __init__(self):
            self.pushed = []

        def upsert(self, records):
            self.pushed.extend(records)
            return len(records)

        def close(self):
            pass

    from src.models import SessionRecord

    def _rec(sid):
        return SessionRecord(session_id=sid, source="claude_cli",
                             model="claude-sonnet-4-6", date="2026-07-01", turns=1)

    fake_sqlite = FakeSqlite([_rec("s1")])
    fake_remote = FakeRemote()

    monkeypatch.setattr(Config, "load", classmethod(lambda cls, **kw: _cfg(tmp_path, "no")))
    monkeypatch.setattr(collect_cmd, "_build_pipeline", lambda cfg: (FakePipeline(), None))
    monkeypatch.setattr(collect_cmd, "_build_stores", lambda cfg: [fake_sqlite, fake_remote])

    parser = tracker.build_parser()
    args = parser.parse_args(["collect"])
    assert args.run(args) == 0

    assert fake_remote.pushed == [_rec("s1")]
    assert fake_sqlite.marked == [("remote_a", ["s1"])]
    captured = capsys.readouterr()
    assert "Synced 1 pending record(s) to remote_a" in captured.out


def test_cmd_collect_no_sweep_when_no_remote_stores(tmp_path, monkeypatch, capsys):
    class FakeResult:
        errors = ()
        stores_failed = ()
        records_written = 0
        collectors_run = 0

    class FakePipeline:
        def since(self, _):
            return self

        def stores(self, *_):
            return self

        def run(self):
            return FakeResult()

    class SpyStore:
        def close(self):
            pass

    monkeypatch.setattr(Config, "load", classmethod(lambda cls, **kw: _cfg(tmp_path, "no")))
    monkeypatch.setattr(collect_cmd, "_build_pipeline", lambda cfg: (FakePipeline(), None))
    monkeypatch.setattr(collect_cmd, "_build_stores", lambda cfg: [SpyStore()])

    parser = tracker.build_parser()
    args = parser.parse_args(["collect"])
    assert args.run(args) == 0
    captured = capsys.readouterr()
    assert "Synced" not in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tracker_cli.py -v -k "sweeps_unsynced or no_sweep"`
Expected: FAIL — `test_cmd_collect_sweeps_unsynced_records` fails because nothing pushes `fake_remote` or marks `fake_sqlite` today (`fake_remote.pushed == []`, no "Synced" text printed). `test_cmd_collect_no_sweep_when_no_remote_stores` should already pass — confirms it's not a false positive.

- [ ] **Step 3: Implement the sweep in `CollectCommand.run()`**

Edit `src/commands/collect.py`:

```python
"""The `collect` subcommand: gather usage from local logs into the store."""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from src.collectors import ClaudeCliCollector, CopilotCliCollector
from src.commands.common import load_remote_stores, run_sync
from src.config import Config
from src.middleware import ModelNormalizeMiddleware
from src.pipeline import TrackerPipeline
from src.project_identity import (
    PROJECT_NAME_MODES,
    ProjectIdentityStore,
    ProjectNameResolver,
)
from src.stores.sqlite import SqliteStore


def _build_pipeline(cfg: Config) -> tuple[TrackerPipeline, ProjectIdentityStore | None]:
    """Build the collection pipeline plus the identity store it borrows (if any).

    The caller owns the returned store and must close it after the run.
    """
    paths = cfg.paths
    mode = cfg.track_project_names
    identity_store = (
        ProjectIdentityStore(cfg.db_path) if mode in ("no", "whimsical") else None
    )
    resolver = ProjectNameResolver(mode, identity_store)
    pipeline = (
        TrackerPipeline()
        .context(cfg.context)
        .add(CopilotCliCollector(paths.copilot_home, resolver=resolver))
        .add(ClaudeCliCollector(paths.claude_projects, resolver=resolver))
        .middlewares(ModelNormalizeMiddleware())
    )
    return pipeline, identity_store


def _build_stores(cfg: Config) -> list:
    return [SqliteStore(cfg.db_path), *load_remote_stores(cfg)]


class CollectCommand:
    name = "collect"
    help = "collect usage from local logs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--lookback", type=int, default=3,
                            help="days of history to collect (default: 3)")
        parser.add_argument("--project-mode", dest="project_mode",
                            choices=list(PROJECT_NAME_MODES), default=None,
                            help="project naming: yes=real name, no=guid, "
                                 "whimsical=masked name (override toml)")
        parser.add_argument("--context", default=None,
                            help='usage context label, e.g. "work" or "personal" '
                                 "(override toml)")

    def run(self, args: argparse.Namespace) -> int:
        overrides = {}
        if args.project_mode is not None:
            overrides["track_project_names"] = args.project_mode
        cfg = Config.load(**overrides)
        cfg = Config(
            paths=cfg.paths,
            db_path=Path(args.db) if args.db else cfg.db_path,
            lookback_days=args.lookback,
            track_project_names=cfg.track_project_names,
            context=args.context if args.context else cfg.context,
            remote_stores=cfg.remote_stores,
        )

        since = date.today() - timedelta(days=cfg.lookback_days)
        pipeline, identity_store = _build_pipeline(cfg)
        stores = _build_stores(cfg)
        try:
            result = pipeline.since(since).stores(*stores).run()
        finally:
            if identity_store is not None:
                identity_store.close()

        for err in result.errors:
            print(f"Warning: {err}", file=sys.stderr)
        for err in result.stores_failed:
            print(f"Warning [store]: {err}", file=sys.stderr)

        if len(stores) > 1:
            sync_result = run_sync(stores[0], stores[1:], dry_run=False)
            for store_name, info in sync_result.items():
                if info.get("pushed"):
                    print(f"Synced {info['pushed']} pending record(s) to {store_name}")

        print(
            f"Collected {result.records_written} session records "
            f"from {result.collectors_run} collectors "
            f"(since {since.isoformat()})"
        )
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tracker_cli.py -v`
Expected: PASS (all tests in file, including the 2 new ones)

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/commands/collect.py tests/test_tracker_cli.py
git commit -m "feat: collect sweeps unsynced records after the inline push"
```

---

### Task 4: Redirect `register-task.ps1` output to a log file

**Files:**
- Modify: `register-task.ps1`

**Interfaces:**
- Consumes: nothing from earlier tasks (independent of Tasks 1-3).
- Produces: a `tracker.log` file next to the db (`$HOME\.tokentracer\tracker.log` for packaged installs, `$PSScriptRoot\tracker.log` for repo checkouts), containing everything the scheduled `collect` run writes to stdout/stderr — including the new `Synced ...` / `Warning [...]` lines from Tasks 2-3.

There is no Python/pytest coverage for `.ps1` files in this repo (no CI for PowerShell here, per the design doc's Testing section) — this task is verified by inspection and, if the user has Windows or PowerShell Core available, a manual dry run.

- [ ] **Step 1: Read the current file to confirm line numbers before editing**

Run: `cat -n register-task.ps1` (or use the Read tool) — confirm the two branches (packaged vs. repo checkout, lines ~12-38) and the action/registration block (lines ~40-64) still match what's quoted below. If line numbers drifted, adjust the edit anchors accordingly.

- [ ] **Step 2: Add log path computation and wrap the action in `cmd.exe` redirection**

Edit `register-task.ps1`. Replace the whole file with:

```powershell
# Registers ai-token-tracer as a Windows Scheduled Task.
# Runs "tokentracer collect --lookback 1" daily at 23:50 if the packaged
# command is on PATH (pipx / uv tool install); otherwise falls back to
# running tracker.py from this repo checkout with the python on PATH.
# Run as your normal user (no admin required). Safe to re-run: an existing
# task with the same name is fully replaced.
# To remove:  Unregister-ScheduledTask -TaskName "ai-token-tracer" -Confirm:$false

$taskName = "ai-token-tracer"

# -- Locate the command to run ----------------------------------------------
$tokentracer = Get-Command tokentracer -ErrorAction SilentlyContinue
if ($tokentracer) {
    # Packaged install: console script on PATH; db defaults to ~\.tokentracer\usage.db
    $exe      = $tokentracer.Source
    $argument = "collect --lookback 1"
    $workDir  = $HOME
    $dbHint   = "$HOME\.tokentracer\usage.db"
    $logDir   = "$HOME\.tokentracer"
    Write-Host "Using packaged tokentracer: $exe"
} else {
    # Repo checkout: run tracker.py next to this script with python on PATH
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $python) {
        Write-Error "Neither 'tokentracer' nor 'python' found on PATH"
        exit 1
    }
    $scriptPath = Join-Path $PSScriptRoot "tracker.py"
    if (-not (Test-Path $scriptPath)) {
        Write-Error "tracker.py not found at: $scriptPath"
        exit 1
    }
    $exe      = $python.Source
    $argument = "`"$scriptPath`" collect --lookback 1"
    $workDir  = $PSScriptRoot
    $dbHint   = "$PSScriptRoot\usage.db"
    $logDir   = $PSScriptRoot
    Write-Host "Using repo checkout: $scriptPath (python: $exe)"
}

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$logPath = Join-Path $logDir "tracker.log"

# -- Build task components -------------------------------------------------
# Wrapped in cmd.exe so stdout/stderr land in tracker.log, since
# New-ScheduledTaskAction has no native output-redirection option.
$wrappedArgument = "/c `"$exe $argument >> `"$logPath`" 2>&1`""
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument $wrappedArgument `
    -WorkingDirectory $workDir

# Run daily at 11:50 PM
$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "23:50"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew

# -- Register (replaces any existing task with the same name) ---------------
Register-ScheduledTask `
    -TaskName    $taskName `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Description "Collects AI tool token usage (Copilot CLI, Claude Code CLI) daily into $dbHint" `
    -Force | Out-Null

Write-Host ""
Write-Host "Task registered: $taskName"
Write-Host "  Runs daily at 23:50 | lookback 1 day | db -> $dbHint"
Write-Host "  Log: $logPath"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now  : Start-ScheduledTask -TaskName '$taskName'"
Write-Host "  Last result: (Get-ScheduledTaskInfo -TaskName '$taskName').LastTaskResult"
Write-Host "  Remove     : Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
```

- [ ] **Step 3: Verify syntax (if PowerShell is available)**

Run (on a machine with PowerShell/pwsh): `pwsh -NoProfile -Command "[System.Management.Automation.Language.Parser]::ParseFile('register-task.ps1', [ref]$null, [ref]$null) | Out-Null; Write-Host 'OK'"`
Expected: `OK` with no parse errors. If no PowerShell is available in this environment (this repo's dev machine is macOS/Darwin), skip this step — note in the commit message that it's unverified by execution and was reviewed by inspection against the working bash equivalent's redirection approach.

- [ ] **Step 4: Commit**

```bash
git add register-task.ps1
git commit -m "fix: redirect Windows scheduled task output to tracker.log"
```

---

## Final Verification

- [ ] Run the full suite once more after all four tasks: `python3 -m pytest -q` — expect all green.
- [ ] Manually run `python3 tracker.py collect --lookback 1` twice in a row against a throwaway db (`--db /tmp/verify.db`) with a `[stores.supabase]`-style stub misconfigured (e.g. bad `class_path`) to confirm: first run logs a `Warning [store]: ...` (or `Warning: could not load store ...`) without crashing; if a valid remote is configured instead, confirm the second run's sweep prints nothing (nothing pending) while `report --detailed`'s `Synced` column reads correctly for pushed rows.
