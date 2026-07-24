# Native `schedule` / `unschedule` Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tokentracer schedule HH:MM` / `tokentracer unschedule` commands that natively register/remove the daily `collect --lookback 1` job on macOS (launchd) and Windows (Task Scheduler), replacing `register-task.sh` / `register-task.ps1`.

**Architecture:** A platform-agnostic `src/schedule.py` module holds pure/OS-shelling functions (`parse_time`, `resolve_executable`, `build_plist`, `schedule_macos`/`unschedule_macos`, `schedule_windows`/`unschedule_windows`). `src/commands/schedule.py` wraps these in two `Command`-protocol classes (`ScheduleCommand`, `UnscheduleCommand`) that branch on `platform.system()` and translate errors into exit codes. Module-level path constants (`_PLIST_PATH`, `_LOG_PATH`) follow the same `monkeypatch.setattr` testability pattern already used by `src/config.py`'s `_TOML_PATH`.

**Tech Stack:** Python 3.11+ stdlib only (`subprocess`, `platform`, `shutil`, `pathlib`), `pytest` + `monkeypatch` for tests.

## Global Constraints

- Python 3.11+ only, stdlib only at runtime (per README) — no new dependencies.
- Follow the existing `Command` protocol exactly (`name`, `help`, `configure(parser)`, `run(args) -> int`) — see `src/commands/base.py`.
- New commands register in `COMMANDS` in `src/commands/__init__.py`, alphabetically consistent with existing ordering by import.
- The scheduled job is always exactly `collect --lookback 1` — not configurable via flags.
- Tests must mock `subprocess.run` and `platform.system()` — never invoke real `launchctl`/`schtasks`. Use `monkeypatch.setattr` on module-level path constants for file-system assertions (mirrors `tests/test_config.py`).
- Every new/changed test must pass alongside the full existing suite (`python3 -m pytest -q` from repo root).

---

### Task 1: `parse_time` and `resolve_executable` helpers

**Files:**
- Create: `src/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produces: `parse_time(value: str) -> tuple[int, int]` — raises `ValueError` on malformed input; used by Task 5's command classes and Task 2/3's schedule functions.
- Produces: `resolve_executable() -> list[str]` — returns an argv prefix (`["tokentracer"]` or `[sys.executable, "<path to tracker.py>"]`); used by Task 2 and Task 3.

- [ ] **Step 1: Write failing tests**

Create `tests/test_schedule.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src import schedule


def test_parse_time_valid():
    assert schedule.parse_time("23:50") == (23, 50)
    assert schedule.parse_time("00:00") == (0, 0)
    assert schedule.parse_time("9:05") == (9, 5)


def test_parse_time_rejects_bad_format():
    with pytest.raises(ValueError):
        schedule.parse_time("2350")
    with pytest.raises(ValueError):
        schedule.parse_time("not-a-time")


def test_parse_time_rejects_out_of_range():
    with pytest.raises(ValueError):
        schedule.parse_time("24:00")
    with pytest.raises(ValueError):
        schedule.parse_time("10:60")


def test_resolve_executable_prefers_path(monkeypatch):
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/local/bin/tokentracer")
    assert schedule.resolve_executable() == ["/usr/local/bin/tokentracer"]


def test_resolve_executable_falls_back_to_tracker_py(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule.shutil, "which", lambda name: None)
    fake_tracker = tmp_path / "tracker.py"
    fake_tracker.write_text("# tracker")
    monkeypatch.setattr(schedule, "_TRACKER_PY_PATH", fake_tracker)
    assert schedule.resolve_executable() == [sys.executable, str(fake_tracker)]


def test_resolve_executable_raises_when_neither_found(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule.shutil, "which", lambda name: None)
    monkeypatch.setattr(schedule, "_TRACKER_PY_PATH", tmp_path / "missing_tracker.py")
    with pytest.raises(FileNotFoundError):
        schedule.resolve_executable()
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_schedule.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.schedule'`

- [ ] **Step 3: Write minimal implementation**

Create `src/schedule.py`:

```python
"""Platform-native scheduling helpers for the daily collect job.

Shared by src/commands/schedule.py's ScheduleCommand/UnscheduleCommand.
Kept OS-agnostic here; the command layer decides which platform functions
to call based on platform.system().
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.ai-token-tracer"
TASK_NAME = "ai-token-tracer"

_TRACKER_PY_PATH = Path(__file__).resolve().parent.parent / "tracker.py"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
_LOG_PATH = Path.home() / ".tokentracer" / "tracker.log"

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_time(value: str) -> tuple[int, int]:
    """Parse an "HH:MM" string into (hour, minute); raises ValueError if invalid."""
    match = _TIME_RE.match(value)
    if not match:
        raise ValueError(f"invalid time {value!r}; expected HH:MM (24-hour)")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ValueError(f"invalid time {value!r}; hour must be 0-23, minute 0-59")
    return hour, minute


def resolve_executable() -> list[str]:
    """Return the argv prefix to invoke tokentracer.

    Prefers the packaged console script on PATH; falls back to running
    tracker.py from this repo checkout with the current interpreter.
    """
    on_path = shutil.which("tokentracer")
    if on_path:
        return [on_path]
    if not _TRACKER_PY_PATH.exists():
        raise FileNotFoundError(
            f"neither 'tokentracer' on PATH nor tracker.py at {_TRACKER_PY_PATH}"
        )
    return [sys.executable, str(_TRACKER_PY_PATH)]
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_schedule.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/schedule.py tests/test_schedule.py
git commit -m "Add parse_time and resolve_executable schedule helpers"
```

---

### Task 2: macOS launchd schedule/unschedule functions

**Files:**
- Modify: `src/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Consumes: `parse_time`, `resolve_executable`, `_PLIST_PATH`, `_LOG_PATH`, `LABEL` from Task 1.
- Produces: `build_plist(hour: int, minute: int, prog_args: list[str]) -> str`; `schedule_macos(hour: int, minute: int) -> None`; `unschedule_macos() -> bool` (`True` if a job was removed, `False` if none was registered) — used by Task 5's `ScheduleCommand`/`UnscheduleCommand` when `platform.system() == "Darwin"`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_schedule.py`:

```python
def test_build_plist_contains_expected_fields():
    xml = schedule.build_plist(23, 50, ["tokentracer", "collect", "--lookback", "1"])
    assert "<string>com.ai-token-tracer</string>" in xml
    assert "<integer>23</integer>" in xml
    assert "<integer>50</integer>" in xml
    assert "<string>tokentracer</string>" in xml
    assert "<string>collect</string>" in xml


def test_schedule_macos_writes_plist_and_loads(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(schedule.subprocess, "run",
                         lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0))
    monkeypatch.setattr(schedule, "_PLIST_PATH", tmp_path / "LaunchAgents" / f"{schedule.LABEL}.plist")
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/local/bin/tokentracer")

    schedule.schedule_macos(23, 50)

    assert schedule._PLIST_PATH.exists()
    assert "<integer>23</integer>" in schedule._PLIST_PATH.read_text()
    assert calls[0][:2] == ["launchctl", "unload"]
    assert calls[-1][:2] == ["launchctl", "load"]


def test_unschedule_macos_removes_existing_job(monkeypatch, tmp_path):
    plist = tmp_path / f"{schedule.LABEL}.plist"
    plist.write_text("<plist></plist>")
    monkeypatch.setattr(schedule, "_PLIST_PATH", plist)
    calls = []
    monkeypatch.setattr(schedule.subprocess, "run",
                         lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0))

    result = schedule.unschedule_macos()

    assert result is True
    assert not plist.exists()
    assert calls[0][:2] == ["launchctl", "unload"]


def test_unschedule_macos_noop_when_nothing_registered(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_PLIST_PATH", tmp_path / "missing.plist")
    result = schedule.unschedule_macos()
    assert result is False
```

Add `import subprocess` to the top of `tests/test_schedule.py` (needed for `subprocess.CompletedProcess`).

- [ ] **Step 2: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_schedule.py -v`
Expected: FAIL with `AttributeError: module 'src.schedule' has no attribute 'build_plist'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/schedule.py`:

```python
_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
{prog_args}
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>{log_path}</string>

    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


def build_plist(hour: int, minute: int, prog_args: list[str]) -> str:
    """Render the launchd plist XML for the given time and program arguments."""
    args_xml = "\n".join(f"        <string>{arg}</string>" for arg in prog_args)
    return _PLIST_TEMPLATE.format(
        label=LABEL, prog_args=args_xml, hour=hour, minute=minute, log_path=_LOG_PATH
    )


def schedule_macos(hour: int, minute: int) -> None:
    """Register (or silently replace) the daily launchd job on macOS."""
    prog_args = resolve_executable() + ["collect", "--lookback", "1"]
    plist_xml = build_plist(hour, minute, prog_args)
    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
    _PLIST_PATH.write_text(plist_xml, encoding="utf-8")
    subprocess.run(["launchctl", "load", str(_PLIST_PATH)], check=True, capture_output=True)


def unschedule_macos() -> bool:
    """Remove the daily launchd job on macOS. Returns False if none was registered."""
    if not _PLIST_PATH.exists():
        return False
    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
    _PLIST_PATH.unlink()
    return True
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_schedule.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/schedule.py tests/test_schedule.py
git commit -m "Add macOS launchd schedule/unschedule functions"
```

---

### Task 3: Windows Task Scheduler schedule/unschedule functions

**Files:**
- Modify: `src/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Consumes: `resolve_executable`, `TASK_NAME` from Task 1.
- Produces: `schedule_windows(hour: int, minute: int) -> None`; `unschedule_windows() -> bool` — used by Task 5's command classes when `platform.system() == "Windows"`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_schedule.py`:

```python
def test_schedule_windows_calls_schtasks_create(monkeypatch):
    calls = []
    monkeypatch.setattr(schedule.subprocess, "run",
                         lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0))
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "C:\\tools\\tokentracer.exe")

    schedule.schedule_windows(23, 50)

    assert calls[0][:3] == ["schtasks", "/Create", "/F"]
    assert "ai-token-tracer" in calls[0]
    assert "23:50" in calls[0]


def test_unschedule_windows_deletes_existing_task(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if args[:2] == ["schtasks", "/Query"]:
            return subprocess.CompletedProcess(args, 0)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(schedule.subprocess, "run", fake_run)

    result = schedule.unschedule_windows()

    assert result is True
    assert calls[0][:2] == ["schtasks", "/Query"]
    assert calls[1][:3] == ["schtasks", "/Delete", "/F"]


def test_unschedule_windows_noop_when_nothing_registered(monkeypatch):
    monkeypatch.setattr(schedule.subprocess, "run",
                         lambda args, **kw: subprocess.CompletedProcess(args, 1))
    result = schedule.unschedule_windows()
    assert result is False
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_schedule.py -v`
Expected: FAIL with `AttributeError: module 'src.schedule' has no attribute 'schedule_windows'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/schedule.py`:

```python
def schedule_windows(hour: int, minute: int) -> None:
    """Register (or silently replace, via /F) the daily scheduled task on Windows."""
    prog_args = resolve_executable() + ["collect", "--lookback", "1"]
    exe, *rest = prog_args
    command = " ".join([f'"{exe}"', *rest])
    subprocess.run(
        [
            "schtasks", "/Create", "/F",
            "/SC", "DAILY",
            "/TN", TASK_NAME,
            "/TR", command,
            "/ST", f"{hour:02d}:{minute:02d}",
        ],
        check=True,
        capture_output=True,
    )


def unschedule_windows() -> bool:
    """Remove the daily scheduled task on Windows. Returns False if none was registered."""
    query = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True
    )
    if query.returncode != 0:
        return False
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], check=True, capture_output=True)
    return True
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_schedule.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add src/schedule.py tests/test_schedule.py
git commit -m "Add Windows Task Scheduler schedule/unschedule functions"
```

---

### Task 4: `ScheduleCommand` and `UnscheduleCommand`

**Files:**
- Create: `src/commands/schedule.py`
- Modify: `src/commands/__init__.py`
- Test: `tests/test_schedule_command.py`

**Interfaces:**
- Consumes: `parse_time`, `resolve_executable`, `schedule_macos`, `unschedule_macos`, `schedule_windows`, `unschedule_windows` from `src/schedule.py` (Tasks 1-3); `Command` protocol from `src/commands/base.py`.
- Produces: `ScheduleCommand` (`name = "schedule"`), `UnscheduleCommand` (`name = "unschedule"`) — registered in `COMMANDS`, invoked by `tracker.py`'s argparse dispatch (unchanged, since it iterates `COMMANDS` generically).

- [ ] **Step 1: Write failing tests**

Create `tests/test_schedule_command.py`:

```python
from __future__ import annotations

import argparse

import pytest

from src.commands.schedule import ScheduleCommand, UnscheduleCommand


def _args(**kw):
    ns = argparse.Namespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_schedule_invalid_time_returns_error(monkeypatch, capsys):
    monkeypatch.setattr("src.commands.schedule.platform.system", lambda: "Darwin")
    cmd = ScheduleCommand()
    code = cmd.run(_args(time="25:99"))
    assert code == 1
    assert "invalid time" in capsys.readouterr().err


def test_schedule_dispatches_to_macos(monkeypatch):
    calls = []
    monkeypatch.setattr("src.commands.schedule.platform.system", lambda: "Darwin")
    monkeypatch.setattr("src.commands.schedule.schedule_macos",
                         lambda hour, minute: calls.append((hour, minute)))
    cmd = ScheduleCommand()
    code = cmd.run(_args(time="23:50"))
    assert code == 0
    assert calls == [(23, 50)]


def test_schedule_dispatches_to_windows(monkeypatch):
    calls = []
    monkeypatch.setattr("src.commands.schedule.platform.system", lambda: "Windows")
    monkeypatch.setattr("src.commands.schedule.schedule_windows",
                         lambda hour, minute: calls.append((hour, minute)))
    cmd = ScheduleCommand()
    code = cmd.run(_args(time="06:00"))
    assert code == 0
    assert calls == [(6, 0)]


def test_schedule_unsupported_os_errors(monkeypatch, capsys):
    monkeypatch.setattr("src.commands.schedule.platform.system", lambda: "Linux")
    cmd = ScheduleCommand()
    code = cmd.run(_args(time="23:50"))
    assert code == 1
    assert "not supported" in capsys.readouterr().err


def test_schedule_subprocess_error_returns_1(monkeypatch, capsys):
    import subprocess as sp
    monkeypatch.setattr("src.commands.schedule.platform.system", lambda: "Darwin")

    def boom(hour, minute):
        raise sp.CalledProcessError(1, ["launchctl", "load"])

    monkeypatch.setattr("src.commands.schedule.schedule_macos", boom)
    cmd = ScheduleCommand()
    code = cmd.run(_args(time="23:50"))
    assert code == 1
    assert "Error" in capsys.readouterr().err


def test_unschedule_reports_removed(monkeypatch, capsys):
    monkeypatch.setattr("src.commands.schedule.platform.system", lambda: "Darwin")
    monkeypatch.setattr("src.commands.schedule.unschedule_macos", lambda: True)
    cmd = UnscheduleCommand()
    code = cmd.run(_args())
    assert code == 0
    assert "removed" in capsys.readouterr().out.lower()


def test_unschedule_reports_nothing_registered(monkeypatch, capsys):
    monkeypatch.setattr("src.commands.schedule.platform.system", lambda: "Darwin")
    monkeypatch.setattr("src.commands.schedule.unschedule_macos", lambda: False)
    cmd = UnscheduleCommand()
    code = cmd.run(_args())
    assert code == 0
    assert "no scheduled job" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_schedule_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.commands.schedule'`

- [ ] **Step 3: Write minimal implementation**

Create `src/commands/schedule.py`:

```python
"""The `schedule` / `unschedule` subcommands: native OS-level daily collect job."""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys

from src.schedule import (
    parse_time,
    schedule_macos,
    schedule_windows,
    unschedule_macos,
    unschedule_windows,
)


class ScheduleCommand:
    name = "schedule"
    help = "register a daily 'collect --lookback 1' job at HH:MM"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("time", help="24-hour HH:MM at which to run daily")

    def run(self, args: argparse.Namespace) -> int:
        try:
            hour, minute = parse_time(args.time)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        system = platform.system()
        try:
            if system == "Darwin":
                schedule_macos(hour, minute)
            elif system == "Windows":
                schedule_windows(hour, minute)
            else:
                print(f"Error: scheduling is not supported on {system}", file=sys.stderr)
                return 1
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        print(f"Scheduled daily 'collect --lookback 1' at {args.time}")
        return 0


class UnscheduleCommand:
    name = "unschedule"
    help = "remove the daily scheduled collect job, if any"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        system = platform.system()
        try:
            if system == "Darwin":
                removed = unschedule_macos()
            elif system == "Windows":
                removed = unschedule_windows()
            else:
                print(f"Error: scheduling is not supported on {system}", file=sys.stderr)
                return 1
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        if removed:
            print("Scheduled job removed")
        else:
            print("No scheduled job found")
        return 0
```

Modify `src/commands/__init__.py`:

```python
"""Static registry of CLI subcommands.

Adding a new subcommand: create src/commands/<name>.py implementing the
Command protocol, then append an instance to COMMANDS below.
"""
from __future__ import annotations

from src.commands.base import Command
from src.commands.collect import CollectCommand
from src.commands.config import ConfigCommand
from src.commands.projects import ProjectsCommand
from src.commands.report import ReportCommand
from src.commands.schedule import ScheduleCommand, UnscheduleCommand
from src.commands.sync import SyncCommand

COMMANDS: list[Command] = [
    CollectCommand(),
    ReportCommand(),
    ConfigCommand(),
    ProjectsCommand(),
    ScheduleCommand(),
    UnscheduleCommand(),
    SyncCommand(),
]

__all__ = ["Command", "COMMANDS"]
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_schedule_command.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/commands/schedule.py src/commands/__init__.py tests/test_schedule_command.py
git commit -m "Add ScheduleCommand and UnscheduleCommand, register in CLI"
```

---

### Task 5: Remove old scripts, update README, full suite check

**Files:**
- Delete: `register-task.sh`
- Delete: `register-task.ps1`
- Modify: `README.md`

**Interfaces:**
- None (docs/cleanup only; no code interfaces produced or consumed).

- [ ] **Step 1: Delete the old scripts**

```bash
git rm register-task.sh register-task.ps1
```

- [ ] **Step 2: Update README usage section**

In `README.md`, locate the `## Usage` code block (contains `# Sync unsynced records...` near the bottom) and add a new section right after the `projects` example and before the `sync` example:

```markdown
# Schedule the daily collector natively (macOS launchd / Windows Task Scheduler)
python3 tracker.py schedule 23:50    # registers/replaces a daily "collect --lookback 1" job
python3 tracker.py unschedule        # removes it
```

Also search for any remaining reference to `register-task` in `README.md` and replace it with a reference to `tokentracer schedule` / `tokentracer unschedule`:

```bash
grep -n "register-task" README.md
```

Update each match found to point at the new commands instead of the removed scripts.

- [ ] **Step 3: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: PASS, all tests green (including the 19 new tests from Tasks 1-4)

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Replace register-task scripts with native schedule/unschedule commands"
```

## Self-Review Notes

- **Spec coverage:** Commands + registration (Task 4), macOS behavior incl. silent replace (Task 2), Windows behavior incl. `/F` silent replace (Task 3), validation before touching the OS (Task 1 + Task 4's `ScheduleCommand.run`), unsupported-OS error path (Task 4), executable resolution shared helper (Task 1), old scripts removed + README updated (Task 5), mocked subprocess/platform tests throughout (Tasks 1-4) — all covered.
- **Placeholder scan:** none found.
- **Type consistency:** `parse_time` returns `tuple[int, int]` consistently used as `(hour, minute)` in Tasks 2-4; `resolve_executable` returns `list[str]` consumed identically in `schedule_macos`/`schedule_windows`; `unschedule_macos`/`unschedule_windows` both return `bool`, consumed identically by `UnscheduleCommand.run`.
