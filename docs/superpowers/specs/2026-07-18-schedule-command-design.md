# Native `schedule` / `unschedule` Commands

## Problem

Scheduling the daily collector currently requires locating and running a separate shell script (`register-task.sh` on macOS, `register-task.ps1` on Windows) outside the `tokentracer` CLI. This is an extra discovery step for users, and the scripts duplicate install-location detection logic (PATH vs. repo checkout) that the rest of the CLI doesn't need to reason about. Goal: fold scheduling into the CLI itself as `tokentracer schedule <HH:MM>` / `tokentracer unschedule`, matching the existing fixed job (`collect --lookback 1` once daily) that the scripts already register.

## Design

### Commands — `src/commands/schedule.py`

Two new `Command` implementations registered in `COMMANDS` (`src/commands/__init__.py`), following the existing Command protocol (`name`, `help`, `configure(parser)`, `run(args) -> int`):

- **`ScheduleCommand`** (`tokentracer schedule HH:MM`): registers a daily OS-native scheduled job that runs `collect --lookback 1` at the given time. The lookback and subcommand are fixed — not configurable — matching current script behavior.
- **`UnscheduleCommand`** (`tokentracer unschedule`): removes the job if present; no-op with a friendly message if nothing is registered.

Implemented as native Python (`platform.system()` branch), shelling out only to `launchctl` (macOS) / `schtasks` (Windows) — not as wrappers around the existing shell/PowerShell scripts. This avoids the "locate the script file relative to install location" problem that pipx/uv installs make fragile, and keeps the feature unit-testable like the rest of the codebase (mock `subprocess.run` / `platform.system()`).

### Platform behavior

- **macOS**: writes `~/Library/LaunchAgents/com.ai-token-tracer.plist` with `ProgramArguments` pointing at the resolved `tokentracer` executable (PATH) or falling back to `[sys.executable, <repo tracker.py path>]` for checkout installs, args `collect --lookback 1`; `StartCalendarInterval` `Hour`/`Minute` parsed from `HH:MM`; `RunAtLoad` false; stdout/stderr redirected to the existing tracker log path. `schedule` runs `launchctl unload` (ignoring failure if nothing was loaded) then `launchctl load` — silent replace, matching current script behavior. `unschedule` runs `launchctl unload` then deletes the plist file.
- **Windows**: uses `schtasks /Create /F ...` (the `/F` flag silently replaces any existing task of the same name — no separate unload step needed) with a daily trigger at `HH:MM`, action fixed to `collect --lookback 1` via the resolved `tokentracer` executable or `python`/`python3` + `tracker.py` fallback. `unschedule` runs `schtasks /Delete /F /TN <task-name>`.
- **Unsupported OS** (anything other than Darwin/Windows): `schedule`/`unschedule` print a clear error and return a non-zero exit code rather than guessing at a scheduling mechanism.

### Validation & error handling

- `HH:MM` is validated (format, hour 0-23, minute 0-59) before touching the OS; invalid input produces a usage-style error and non-zero exit, no partial state written.
- If `launchctl`/`schtasks` is missing or exits non-zero, `run()` prints the underlying error and returns non-zero — no silent failure.
- `unschedule` when nothing is registered prints a friendly "no scheduled job found" message and returns 0 (not an error condition).

### Executable resolution

Both platforms need to locate what to actually run. Reuse the same detection the existing scripts already do: check if `tokentracer` resolves on PATH (packaged install); if not, fall back to `sys.executable` + the path to `tracker.py` in the current repo checkout. This logic is extracted into a small shared helper in `schedule.py` used by both `ScheduleCommand` and any plist/task-XML generation, rather than duplicated per-OS.

### Migration — old scripts removed

`register-task.sh` and `register-task.ps1` are deleted as part of this change (not kept in parallel). `README.md` is updated to reference `tokentracer schedule <HH:MM>` / `tokentracer unschedule` instead.

### Testing

- Unit tests mock `subprocess.run` and `platform.system()` to verify: correct plist XML content is generated and written for macOS; correct `schtasks` arguments are built for Windows; `HH:MM` validation rejects malformed input; `unschedule` handles the "nothing registered" case without erroring; unsupported-OS path returns non-zero. No real `launchctl`/`schtasks` invocation in the test suite.

## Out of scope

- Linux/systemd support (existing scripts only ever covered macOS + Windows; parity is the target, not expansion).
- A `status`/`list` command to check whether a job is currently registered (not requested; `schedule` already silently replaces, so there's no need to check first).
- Configurable lookback or subcommand for the scheduled job — fixed to `collect --lookback 1`, matching current behavior exactly.
