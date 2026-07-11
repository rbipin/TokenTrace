# Collect/Sync Unification Design

## Problem

`collect` already pushes newly-collected records to configured remote stores inline, inside `TrackerPipeline.run()` (`src/pipeline.py`). But that inline push never calls `SqliteStore.mark_synced()`, so:

- `sessions`/`sync_log` never records that those rows reached the remote — `tracker.py report --detailed`'s Synced column is wrong for every row `collect` ever pushed.
- A later `tokentracer sync` run re-pushes those same rows again (harmless, since remote stores upsert, but wasteful and confusing).
- There is no automatic retry path: if a remote store is unreachable during a `collect` run, that failure is only recovered by a human remembering to run `tokentracer sync` later. The scheduled task (`register-task.sh` / `register-task.ps1`) only ever calls `collect`.

## Goals

- Fix the `mark_synced` gap so `collect`'s inline push is correctly tracked.
- Make `collect` self-healing: every run also sweeps and retries anything still marked unsynced (including failures from earlier runs), without requiring a second scheduled job.
- Keep `tokentracer sync` available standalone for manual/dry-run use.
- No changes to the registered scheduled task cadence — it keeps calling `collect` only.
- Ensure sync failures are visible in the log file on both macOS and Windows scheduled runs.

## Non-goals

- Changing the remote store protocol (`SessionStore`) itself.
- Changing collection cadence, lookback defaults, or the `sync --dry-run` UX.
- Retrying failed local (SQLite) writes — those still propagate as hard errors, unchanged.

## Design

### Change 1 — mark_synced on the inline push (`src/pipeline.py`)

In `TrackerPipeline.run()`, after a remote store's `upsert()` succeeds, call:

```python
if hasattr(self._stores[0], "mark_synced"):
    self._stores[0].mark_synced(merged, store.name)
```

Duck-typed rather than an `isinstance(SqliteStore)` check, so `pipeline.py` doesn't need to import `SqliteStore` — it already treats `stores[0]` specially (must-succeed local store) elsewhere in the same method.

### Change 2 — collect also sweeps unsynced records (`src/commands/collect.py`)

After `pipeline.run()` completes (success or with some `stores_failed`), reuse the same store instances built by `_build_stores()` (`stores[0]` = `SqliteStore`, `stores[1:]` = remotes) to run the existing sync-sweep logic against whatever `sync_log` still shows as unsynced.

Because of Change 1, this sweep normally finds nothing — today's records were already marked synced by the inline push. It only catches genuine leftovers: records whose remote push failed on a previous run (this run or an earlier one, whenever the remote was unreachable).

Output stays quiet unless there's something to report, since this runs unattended via the scheduled task and logs to a file:
- Pushed something: `Synced N pending record(s) to <store>`
- Push failed: `Warning [sync:<store>]: <error>` (stderr, same style as existing warnings)
- Nothing pending: no output.

### Change 3 — de-duplicate `_run_sync`

Move `_run_sync` from `src/commands/sync.py` into `src/commands/common.py` (next to `load_remote_stores`, which it's always used alongside). Both `sync.py` and `collect.py` import it from `common.py`; neither reaches into the other's private helpers.

### Change 4 — Windows scheduled task logging (`register-task.ps1`)

`New-ScheduledTaskAction` currently runs `tokentracer`/`python` directly with no output redirection — sync failures (or any output) go nowhere on Windows, unlike macOS where `register-task.sh` already redirects via `StandardOutPath`/`StandardErrorPath` into `tracker.log`.

Fix: wrap the actual command in `cmd.exe /c "... >> tracker.log 2>&1"`.

- Compute `$logDir` using the same branching already used for `$dbHint`: `$HOME\.tokentracer` for a packaged install, `$PSScriptRoot` for a repo checkout. Create it if missing.
- `$logPath = Join-Path $logDir "tracker.log"`.
- Replace the action:
  ```powershell
  $wrappedArgument = "/c `"$exe $argument >> `"$logPath`" 2>&1`""
  $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $wrappedArgument -WorkingDirectory $workDir
  ```
- Add the log path to the closing `Write-Host` summary, matching the bash script's "Log: ..." line.

No other behavior changes to the task (trigger, settings, cadence all unchanged).

## Data flow after this change

```
collect:
  pipeline.run()
    -> collect from sources, merge, normalize
    -> upsert to SqliteStore (must succeed)
    -> for each remote store: upsert; on success, mark_synced in SqliteStore's sync_log
  -> sweep: for each remote store, push whatever sync_log still shows unsynced
            (leftovers from this run's failures or earlier runs); mark_synced on success

sync (manual):
  unchanged — pushes whatever sync_log shows unsynced, same as the sweep collect now runs
```

## Testing

- `test_pipeline_multi_store.py`: assert `mark_synced` is called on the local store after a successful remote push, and NOT called when the remote push fails.
- `test_collect_command` (new or extended): mock a remote store that fails on the first `collect` run, then verify a second `collect` run's sweep retries the leftover record and succeeds (and marks it synced).
- `test_sync_command.py`: update imports for the `common.py` move; behavior unchanged.
- Add a lightweight check (or manual note, since PowerShell isn't testable in this repo's Python test suite) that `register-task.ps1`'s action wraps output redirection correctly — reviewed by inspection since there's no CI for `.ps1` files here.

## Rollout

No migration needed — `sync_log` schema is unchanged. Existing unsynced rows from before this change will simply get swept and correctly marked on the next `collect` run.
