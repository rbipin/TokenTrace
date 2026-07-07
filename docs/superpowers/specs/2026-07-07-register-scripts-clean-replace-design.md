# Register Scripts: Clean Replace on Re-run — Design

**Date**: 2026-07-07
**Scope**: `register-task.ps1`, `register-task.sh`

## Goal

Re-running either registration script must fully replace any existing
scheduled task/job with the current definition (action, trigger, settings,
description) — never partially update it and never create a duplicate.

## Design

### register-task.ps1 (Windows Task Scheduler)

- Remove the `Get-ScheduledTask` existence check and the
  `Set-ScheduledTask` update branch.
- Always call `Register-ScheduledTask ... -Force`.
  - `-Force` replaces an existing task with the same name/path entirely;
    it does not create a duplicate. If no task exists, it creates one.
- Keep the existing path validation for `$pythonExe` and `$scriptPath`.
- Keep the summary output; note in the header comment that re-running the
  script replaces the task.

### register-task.sh (macOS launchd)

Already performs a clean replace: unload existing job → overwrite plist
via `cat >` → `launchctl load`. No functional change required; only align
the message when an existing job is found ("replacing existing job").

## Error Handling

- PS1: unchanged — exits 1 if Python or tracker.py path is missing.
- SH: unchanged — `set -euo pipefail`; unload failures on a stale plist
  are tolerated (`|| true`).

## Testing

No automated tests cover these scripts (environment-dependent helpers).
Validation: parse-check the PowerShell script
(`[scriptblock]::Create((Get-Content -Raw register-task.ps1))`) and
`bash -n register-task.sh` for syntax.
