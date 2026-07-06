# Session-Primary Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the store from day-grain to session-grain, add opt-in project name tracking, surface cache efficiency in reports, and add `--by-project` and `--sessions` report views.

**Architecture:** Replace `ActivityRecord` with `SessionRecord` (one record per session per model). Replace the `usage` SQLite table with `sessions` (same file). All existing day/month queries become `GROUP BY date` aggregations over `sessions`. Two new report modes query the same table with different `GROUP BY` shapes.

**Tech Stack:** Python 3.11+ stdlib only (`tomllib`, `sqlite3`, `json`, `pathlib`). `pytest` for tests.

## Global Constraints

- Standard library only at runtime (no new pip dependencies)
- `tomllib` is stdlib in Python 3.11+ — use `try: import tomllib except ImportError: tomllib = None` for graceful degradation
- Tests use `tmp_path` fixture; never touch real `~/.claude/` or `~/.copilot/`
- `collect` must remain idempotent — re-running with the same data produces the same DB state
- `project` field is `None` whenever `track_project_names=False`; nothing work-sensitive is ever stored
- Run `python3 -m pytest -q` after every task — all tests must pass before the next task

---

### Task 1: Replace ActivityRecord with SessionRecord

**Files:**
- Modify: `src/models.py`
- Modify: `src/collectors/base.py`
- Modify: `src/pipeline.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `SessionRecord(session_id, source, model, date, start_ts, end_ts, project, turns, tool_calls, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, context_peak_tokens, reasoning_tokens)`
- Produces: `merge_records(records: list[SessionRecord]) -> list[SessionRecord]`
- Produces: `SessionRecord.key: tuple[str, str, str]`  →  `(session_id, source, model)`
- Note: merge key is `(session_id, source, model)` — not `(session_id, source)` — to handle Copilot sessions that emit multiple models from one shutdown_event.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py  (replace entire file)
from __future__ import annotations

from src.models import UNKNOWN_MODEL, SessionRecord, merge_records


def _rec(**kwargs) -> SessionRecord:
    defaults = dict(session_id="sess-1", source="claude_cli", model="claude-sonnet-4-6")
    defaults.update(kwargs)
    return SessionRecord(**defaults)


def test_key_is_session_source_model():
    r = _rec(session_id="abc", source="copilot_cli", model="gpt-4o")
    assert r.key == ("abc", "copilot_cli", "gpt-4o")


def test_default_fields():
    r = SessionRecord(session_id="s1", source="claude_cli")
    assert r.model == UNKNOWN_MODEL
    assert r.project is None
    assert r.turns == 0
    assert r.input_tokens == 0
    assert r.cache_creation_tokens == 0
    assert r.cache_read_tokens == 0
    assert r.start_ts is None
    assert r.end_ts is None


def test_merge_deduplicates_by_key():
    a = _rec(session_id="s1", turns=3)
    b = _rec(session_id="s1", turns=5)  # same key, different turns
    result = merge_records([a, b])
    assert len(result) == 1
    assert result[0].turns == 5  # last writer wins


def test_merge_keeps_distinct_keys():
    a = _rec(session_id="s1", model="claude-sonnet-4-6")
    b = _rec(session_id="s1", model="claude-opus-4-8")  # same session, different model
    c = _rec(session_id="s2", model="claude-sonnet-4-6")
    result = merge_records([a, b, c])
    assert len(result) == 3


def test_merge_empty():
    assert merge_records([]) == []
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_models.py -v
```
Expected: multiple failures (`SessionRecord not defined`, etc.)

- [ ] **Step 3: Replace src/models.py**

```python
# src/models.py
from __future__ import annotations

from dataclasses import dataclass

UNKNOWN_MODEL = "unknown"


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    source: str
    model: str = UNKNOWN_MODEL
    date: str = ""
    start_ts: str | None = None
    end_ts: str | None = None
    project: str | None = None
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    context_peak_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.session_id, self.source, self.model)


def merge_records(records: list[SessionRecord]) -> list[SessionRecord]:
    """Deduplicate by (session_id, source, model). Last writer wins."""
    merged: dict[tuple[str, str, str], SessionRecord] = {}
    for rec in records:
        merged[rec.key] = rec
    return list(merged.values())
```

- [ ] **Step 4: Update src/collectors/base.py — change return type annotation**

Find the `ActivityCollector` Protocol. Change the `collect` return type from `Iterable[ActivityRecord]` to `Iterable[SessionRecord]` and update the import. The function signature stays the same. Example:

```python
# src/collectors/base.py  (full replacement)
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterator, Protocol

from ..models import SessionRecord


class ActivityCollector(Protocol):
    """Read-only source that yields session records."""

    source: str

    def collect(self, since: date) -> Iterator[SessionRecord]:
        ...


# ── timestamp helpers ────────────────────────────────────────────────────────

def _parse_ts(ts: object) -> datetime | None:
    """Parse a timestamp that may be ISO-8601 string or epoch ms/s float."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        seconds = ts / 1000 if ts > 1e11 else ts
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def to_date(ts: object) -> date | None:
    """Convert any timestamp form to a local date, or None on failure."""
    dt = _parse_ts(ts)
    return dt.astimezone().date() if dt else None


def to_local_iso(ts: object) -> str | None:
    """Convert any timestamp form to a local ISO-8601 string, or None."""
    dt = _parse_ts(ts)
    return dt.astimezone().replace(microsecond=0).isoformat() if dt else None
```

- [ ] **Step 5: Update src/pipeline.py — change type annotations**

Find the two occurrences of `ActivityRecord` and replace with `SessionRecord`, and update the import:

```python
# Change the import line from:
from .models import ActivityRecord, merge_records
# to:
from .models import SessionRecord, merge_records

# Change the type annotation in run():
# from:  records: list[ActivityRecord] = []
# to:    records: list[SessionRecord] = []

# Change the _collect return type:
# from:  def _collect(collector) -> tuple[list[ActivityRecord], str | None]:
# to:    def _collect(collector) -> tuple[list[SessionRecord], str | None]:
```

- [ ] **Step 6: Run the test**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_models.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 7: Run full suite to check for breakage**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest -q
```
Expected: test_models.py passes; other tests may fail because they still use `ActivityRecord` — that is expected and will be fixed in subsequent tasks.

- [ ] **Step 8: Commit**

```bash
cd /Users/bipin/repo/TokenTracer && git add src/models.py src/collectors/base.py src/pipeline.py tests/test_models.py && git commit -m "refactor: replace ActivityRecord with SessionRecord, key=(session_id,source,model)"
```

---

### Task 2: Config — TOML loading and `config set` subcommand

**Files:**
- Modify: `src/config.py`
- Modify: `tracker.py` (add `config set` subcommand only; other tracker changes come in Task 7)
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `Config.load(**overrides) -> Config` — reads `~/.tokentracer.toml`, applies keyword overrides
- Produces: `Config.track_project_names: bool`
- Produces: `write_toml_setting(key: str, value: bool) -> None` in `src/config.py`
- Produces: `tracker.py config set <key> <value>` subcommand

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py  (new file)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.config import Config, write_toml_setting


def test_default_track_project_names_is_false():
    cfg = Config()
    assert cfg.track_project_names is False


def test_load_returns_defaults_when_no_toml(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / "no_such.toml")
    cfg = Config.load()
    assert cfg.track_project_names is False


def test_load_reads_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = true\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names is True


def test_load_override_wins_over_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = true\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load(track_project_names=False)
    assert cfg.track_project_names is False


def test_load_invalid_toml_falls_back_to_defaults(tmp_path, monkeypatch, capsys):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("NOT VALID TOML @@@@")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names is False
    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_write_toml_setting_creates_file(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    write_toml_setting("track_project_names", True)
    assert toml.exists()
    content = toml.read_text()
    assert "track_project_names = true" in content


def test_write_toml_setting_updates_existing(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = false\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    write_toml_setting("track_project_names", True)
    content = toml.read_text()
    assert "track_project_names = true" in content
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_config.py -v
```
Expected: ImportError on `write_toml_setting` and failures on `Config.load`.

- [ ] **Step 3: Replace src/config.py**

```python
# src/config.py
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    tomllib = None  # type: ignore[assignment]

_TOML_PATH = Path.home() / ".tokentracer.toml"


@dataclass(frozen=True)
class Paths:
    copilot_home: Path = field(default_factory=lambda: Path.home() / ".copilot")
    claude_projects: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "projects"
    )


@dataclass(frozen=True)
class Config:
    paths: Paths = field(default_factory=Paths)
    db_path: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[1] / "usage.db"
    )
    lookback_days: int = 3
    track_project_names: bool = False

    @classmethod
    def load(cls, **overrides) -> "Config":
        """Load from ~/.tokentracer.toml, then apply keyword overrides."""
        base: dict = {}
        if tomllib is not None and _TOML_PATH.exists():
            try:
                with open(_TOML_PATH, "rb") as fh:
                    data = tomllib.load(fh)
                tracking = data.get("tracking", {})
                if "track_project_names" in tracking:
                    base["track_project_names"] = bool(tracking["track_project_names"])
            except Exception as exc:
                print(f"Warning: could not parse ~/.tokentracer.toml: {exc}", file=sys.stderr)
        base.update(overrides)
        return cls(**base)


def write_toml_setting(key: str, value: bool) -> None:
    """Merge one [tracking] key into ~/.tokentracer.toml (no external deps)."""
    existing: dict[str, dict] = {}
    if tomllib is not None and _TOML_PATH.exists():
        try:
            with open(_TOML_PATH, "rb") as fh:
                raw = tomllib.load(fh)
            for section, vals in raw.items():
                if isinstance(vals, dict):
                    existing[section] = dict(vals)
        except Exception:
            pass
    existing.setdefault("tracking", {})[key] = value
    lines: list[str] = []
    for section, vals in existing.items():
        lines.append(f"[{section}]")
        for k, v in vals.items():
            lines.append(f"{k} = {str(v).lower()}")
        lines.append("")
    _TOML_PATH.write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 4: Add `config set` subcommand to tracker.py**

In `tracker.py`, add an import at the top:

```python
from src.config import Config, write_toml_setting
```

Add this function before `if __name__ == "__main__":`:

```python
def cmd_config_set(args) -> int:
    supported = {"track_project_names"}
    if args.key not in supported:
        print(
            f"Unknown config key: {args.key!r}. Supported: {', '.join(supported)}",
            file=sys.stderr,
        )
        return 1
    bool_val = args.value.lower() in ("1", "true", "yes")
    write_toml_setting(args.key, bool_val)
    print(f"Set {args.key} = {bool_val} in ~/.tokentracer.toml")
    return 0
```

In the argument parser section, after the existing subparsers, add:

```python
# config subparser
p_config = sub.add_parser("config", help="manage configuration")
config_sub = p_config.add_subparsers(dest="config_cmd")
p_config_set = config_sub.add_parser("set", help="set a config value")
p_config_set.add_argument("key", help="config key (e.g. track_project_names)")
p_config_set.add_argument("value", help="value (true/false/1/0/yes/no)")
```

In the dispatch section, add:

```python
elif args.cmd == "config":
    if args.config_cmd == "set":
        sys.exit(cmd_config_set(args))
    else:
        p_config.print_help()
        sys.exit(1)
```

- [ ] **Step 5: Run config tests**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_config.py -v
```
Expected: all 7 tests PASS

- [ ] **Step 6: Smoke-test the CLI**

```bash
cd /Users/bipin/repo/TokenTracer && python3 tracker.py config set track_project_names false
```
Expected: `Set track_project_names = false in ~/.tokentracer.toml` (or a warning if Python < 3.11)

- [ ] **Step 7: Commit**

```bash
cd /Users/bipin/repo/TokenTracer && git add src/config.py tracker.py tests/test_config.py && git commit -m "feat: add TOML config loading and 'config set' subcommand"
```

---

### Task 3: Replace usage table with sessions table

**Files:**
- Modify: `src/store.py`
- Modify: `tests/test_store_report.py` (store portion only; report queries updated in Task 6)

**Interfaces:**
- Consumes: `SessionRecord` from Task 1
- Produces: `UsageStore(db_path: Path)` — auto-migrates on init
- Produces: `UsageStore.upsert(records: list[SessionRecord]) -> int`
- Produces: `sessions` table with PRIMARY KEY `(session_id, source, model)`

- [ ] **Step 1: Write failing store tests**

```python
# tests/test_store_report.py  (replace entire file — report tests added in Task 6)
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.models import SessionRecord
from src.store import UsageStore


def _rec(session_id: str, date_str: str = "2026-07-01", **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-sonnet-4-6", date=date_str)
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def test_upsert_and_count(tmp_db):
    store = UsageStore(tmp_db)
    n = store.upsert([_rec("s1"), _rec("s2")])
    assert n == 2


def test_upsert_is_idempotent(tmp_db):
    store = UsageStore(tmp_db)
    store.upsert([_rec("s1", input_tokens=100)])
    store.upsert([_rec("s1", input_tokens=200)])  # re-collect same session
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT input_tokens FROM sessions WHERE session_id='s1'").fetchone()
    assert row[0] == 200  # last write wins


def test_upsert_different_models_same_session(tmp_db):
    store = UsageStore(tmp_db)
    store.upsert([
        _rec("s1", model="claude-sonnet-4-6", turns=3),
        _rec("s1", model="claude-opus-4-8", turns=1),
    ])
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT model FROM sessions WHERE session_id='s1' ORDER BY model").fetchall()
    assert len(rows) == 2


def test_migration_drops_old_usage_table(tmp_db, capsys):
    import sqlite3
    # Create old-style usage table
    conn = sqlite3.connect(tmp_db)
    conn.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    # Init store — should migrate
    UsageStore(tmp_db)
    captured = capsys.readouterr()
    assert "usage" in captured.err  # migration warning printed
    conn2 = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "sessions" in tables
    assert "usage" not in tables


def test_project_stored_when_set(tmp_db):
    store = UsageStore(tmp_db)
    store.upsert([_rec("s1", project="MyApp")])
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT project FROM sessions WHERE session_id='s1'").fetchone()
    assert row[0] == "MyApp"


def test_project_null_when_not_set(tmp_db):
    store = UsageStore(tmp_db)
    store.upsert([_rec("s1")])
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT project FROM sessions WHERE session_id='s1'").fetchone()
    assert row[0] is None
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_store_report.py -v
```
Expected: ImportError or schema failures.

- [ ] **Step 3: Replace src/store.py**

```python
# src/store.py
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from .models import SessionRecord

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id             TEXT NOT NULL,
    source                 TEXT NOT NULL,
    model                  TEXT NOT NULL,
    date                   TEXT NOT NULL,
    start_ts               TEXT,
    end_ts                 TEXT,
    project                TEXT,
    turns                  INTEGER DEFAULT 0,
    tool_calls             INTEGER DEFAULT 0,
    input_tokens           INTEGER DEFAULT 0,
    output_tokens          INTEGER DEFAULT 0,
    cache_creation_tokens  INTEGER DEFAULT 0,
    cache_read_tokens      INTEGER DEFAULT 0,
    context_peak_tokens    INTEGER DEFAULT 0,
    reasoning_tokens       INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, source, model)
)
"""

_UPSERT = """
INSERT OR REPLACE INTO sessions
    (session_id, source, model, date, start_ts, end_ts, project,
     turns, tool_calls, input_tokens, output_tokens,
     cache_creation_tokens, cache_read_tokens,
     context_peak_tokens, reasoning_tokens)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class UsageStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _migrate(self) -> None:
        with self._connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "usage" in tables and "sessions" not in tables:
                print(
                    "Warning: dropping old 'usage' table. "
                    "Re-run: python3 tracker.py collect --lookback 90",
                    file=sys.stderr,
                )
                conn.execute("DROP TABLE usage")
            conn.execute(_CREATE_SESSIONS)
            conn.commit()

    def upsert(self, records: list[SessionRecord]) -> int:
        if not records:
            return 0
        with self._connect() as conn:
            conn.executemany(
                _UPSERT,
                [
                    (
                        r.session_id, r.source, r.model, r.date,
                        r.start_ts, r.end_ts, r.project,
                        r.turns, r.tool_calls, r.input_tokens, r.output_tokens,
                        r.cache_creation_tokens, r.cache_read_tokens,
                        r.context_peak_tokens, r.reasoning_tokens,
                    )
                    for r in records
                ],
            )
        return len(records)
```

- [ ] **Step 4: Run store tests**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_store_report.py -v
```
Expected: all 6 store tests PASS

- [ ] **Step 5: Run full suite**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest -q
```
Expected: test_models.py, test_config.py, test_store_report.py pass. Other tests (claude_cli, copilot) fail — expected, fixed in Tasks 4 and 5.

- [ ] **Step 6: Commit**

```bash
cd /Users/bipin/repo/TokenTracer && git add src/store.py tests/test_store_report.py && git commit -m "feat: replace usage table with sessions (session-primary schema)"
```

---

### Task 4: Update ClaudeCliCollector

**Files:**
- Modify: `src/collectors/claude_cli.py`
- Modify: `tests/test_claude_cli_collector.py`

**Interfaces:**
- Consumes: `SessionRecord` from Task 1, `to_date` / `to_local_iso` from `base.py` Task 1
- Produces: `ClaudeCliCollector(projects_dir: Path, track_project_names: bool = False)`
- Produces: one `SessionRecord` per JSONL file (session) where `start_ts.date() >= since`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_claude_cli_collector.py  (replace entire file)
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.collectors.claude_cli import ClaudeCliCollector
from src.models import UNKNOWN_MODEL


def _write_session(path: Path, session_id: str, entries: list[dict]) -> None:
    """Write a JSONL session file under path/<project-dir>/<session_id>.jsonl"""
    proj_dir = path / "proj-1"
    proj_dir.mkdir(exist_ok=True)
    (proj_dir / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
    )


def _asst(ts: str, model: str, input_t: int, output_t: int,
          cache_create: int = 0, cache_read: int = 0,
          cwd: str | None = None) -> dict:
    entry: dict = {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cache_creation_input_tokens": cache_create,
                "cache_read_input_tokens": cache_read,
            },
        },
    }
    if cwd:
        entry["cwd"] = cwd
    return entry


def test_single_session_basic(tmp_path):
    _write_session(tmp_path, "sess-abc", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50),
        _asst("2026-07-03T11:00:00.000Z", "claude-sonnet-4-6", 200, 75),
    ])
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))
    assert len(records) == 1
    r = records[0]
    assert r.session_id == "sess-abc"
    assert r.source == "claude_cli"
    assert r.model == "claude-sonnet-4-6"
    assert r.date == "2026-07-03"
    assert r.turns == 2
    assert r.input_tokens == 300
    assert r.output_tokens == 125


def test_start_end_ts(tmp_path):
    _write_session(tmp_path, "sess-ts", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5),
        _asst("2026-07-03T12:30:00.000Z", "claude-sonnet-4-6", 20, 8),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert "10:00:00" in r.start_ts
    assert "12:30:00" in r.end_ts


def test_cache_tokens_summed(tmp_path):
    _write_session(tmp_path, "sess-cache", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50, cache_create=200, cache_read=800),
        _asst("2026-07-03T11:00:00.000Z", "claude-sonnet-4-6", 50, 25, cache_create=0, cache_read=400),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.cache_creation_tokens == 200
    assert r.cache_read_tokens == 1200


def test_since_filter_excludes_old_session(tmp_path):
    _write_session(tmp_path, "sess-old", [
        _asst("2026-07-01T10:00:00.000Z", "claude-sonnet-4-6", 100, 50),
    ])
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))
    assert records == []


def test_project_captured_when_enabled(tmp_path):
    _write_session(tmp_path, "sess-proj", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5, cwd="/home/user/my-app"),
    ])
    r = list(ClaudeCliCollector(tmp_path, track_project_names=True).collect(date(2026, 7, 3)))[0]
    assert r.project == "my-app"


def test_project_none_when_disabled(tmp_path):
    _write_session(tmp_path, "sess-noproj", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5, cwd="/home/user/work-repo"),
    ])
    r = list(ClaudeCliCollector(tmp_path, track_project_names=False).collect(date(2026, 7, 3)))[0]
    assert r.project is None


def test_empty_session_yields_record_with_zero_tokens(tmp_path):
    """A JSONL with no assistant entries still yields a record (zero tokens)."""
    _write_session(tmp_path, "sess-empty", [
        {"type": "last-prompt", "sessionId": "sess-empty"},
    ])
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 1)))
    # No timestamps → included; zero tokens
    assert len(records) == 1
    assert records[0].turns == 0
    assert records[0].input_tokens == 0


def test_malformed_lines_skipped(tmp_path):
    proj_dir = tmp_path / "proj-x"
    proj_dir.mkdir()
    (proj_dir / "sess-bad.jsonl").write_text(
        "NOT JSON\n" + json.dumps(_asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5)),
        encoding="utf-8",
    )
    records = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))
    assert len(records) == 1
    assert records[0].turns == 1
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_claude_cli_collector.py -v
```
Expected: failures (old collector returns `ActivityRecord`).

- [ ] **Step 3: Replace src/collectors/claude_cli.py**

```python
# src/collectors/claude_cli.py
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterator

from .base import to_date, to_local_iso
from ..models import SessionRecord, UNKNOWN_MODEL


class ClaudeCliCollector:
    """Yields one SessionRecord per JSONL conversation file."""

    source = "claude_cli"

    def __init__(self, projects_dir: Path, track_project_names: bool = False) -> None:
        self._dir = projects_dir
        self._track_projects = track_project_names

    def collect(self, since: date) -> Iterator[SessionRecord]:
        for jsonl_path in self._dir.rglob("*.jsonl"):
            record = self._parse_session(jsonl_path, since)
            if record is not None:
                yield record

    def _parse_session(self, path: Path, since: date) -> SessionRecord | None:
        session_id = path.stem
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }
        model = UNKNOWN_MODEL
        project: str | None = None
        start_ts: str | None = None
        end_ts: str | None = None
        turns = 0

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = entry.get("timestamp")
            if ts:
                if start_ts is None or ts < start_ts:
                    start_ts = ts
                if end_ts is None or ts > end_ts:
                    end_ts = ts

            if project is None and self._track_projects:
                cwd = entry.get("cwd")
                if cwd:
                    project = Path(cwd).name

            if entry.get("type") != "assistant":
                continue

            turns += 1
            msg = entry.get("message") or {}
            if model == UNKNOWN_MODEL and msg.get("model"):
                model = msg["model"]
            usage = msg.get("usage") or {}
            totals["input_tokens"] += usage.get("input_tokens", 0)
            totals["output_tokens"] += usage.get("output_tokens", 0)
            totals["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0)
            totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)

        # Apply since filter on sessions that have a known start date
        if start_ts is not None:
            session_date = to_date(start_ts)
            if session_date is None or session_date < since:
                return None
            date_str = session_date.isoformat()
        else:
            date_str = date.today().isoformat()

        return SessionRecord(
            session_id=session_id,
            source=self.source,
            model=model,
            date=date_str,
            start_ts=to_local_iso(start_ts) if start_ts else None,
            end_ts=to_local_iso(end_ts) if end_ts else None,
            project=project,
            turns=turns,
            **totals,
        )
```

- [ ] **Step 4: Run claude_cli tests**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_claude_cli_collector.py -v
```
Expected: all 8 tests PASS

- [ ] **Step 5: Run full suite**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest -q
```
Expected: test_claude_cli_collector, test_models, test_config, test_store_report pass. Copilot test may still fail — fixed in Task 5.

- [ ] **Step 6: Commit**

```bash
cd /Users/bipin/repo/TokenTracer && git add src/collectors/claude_cli.py tests/test_claude_cli_collector.py && git commit -m "feat: rewrite ClaudeCliCollector to yield one SessionRecord per JSONL"
```

---

### Task 5: Update CopilotCliCollector

**Files:**
- Modify: `src/collectors/copilot_cli.py`
- Modify: `tests/test_cli_collector.py`

**Interfaces:**
- Consumes: `SessionRecord` from Task 1, `to_date` / `to_local_iso` from `base.py`
- Produces: `CopilotCliCollector(copilot_home: Path, track_project_names: bool = False)`
- Produces: one `SessionRecord` per `(copilot_session_id, model)` — multi-model shutdown events yield separate records

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_collector.py  (replace entire file)
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from src.collectors.copilot_cli import CopilotCliCollector


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "copilot"
    home.mkdir()
    db_path = home / "session-store.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            cwd TEXT,
            repository TEXT,
            origin TEXT,
            branch TEXT,
            status TEXT,
            startedAt TEXT,
            endedAt TEXT
        )
    """)
    conn.commit()
    conn.close()
    return home


def _add_session(home: Path, id_: str, cwd: str, repo: str,
                 started: str, ended: str) -> None:
    conn = sqlite3.connect(home / "session-store.db")
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, 'github', 'main', 'sum', ?, ?)",
        (id_, cwd, repo, started, ended),
    )
    conn.commit()
    conn.close()


def _write_events(home: Path, session_id: str, events: list[dict]) -> None:
    state_dir = home / "session-state" / session_id
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


def _shutdown(model_metrics: dict) -> dict:
    return {
        "type": "session.shutdown",
        "modelMetrics": {
            m: {
                "sessions": 1 if i == 0 else 0,
                "turns": v.get("turns", 0),
                "inputTokens": v.get("input", 0),
                "outputTokens": v.get("output", 0),
                "cacheReadTokens": v.get("cache_read", 0),
                "cacheWriteTokens": v.get("cache_write", 0),
                "reasoningTokens": v.get("reasoning", 0),
            }
            for i, (m, v) in enumerate(model_metrics.items())
        },
    }


def test_basic_shutdown_session(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/myapp", "owner/myapp",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 3, "input": 1000, "output": 200,
                                          "cache_read": 500, "cache_write": 100}}),
    ])
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))
    assert len(records) == 1
    r = records[0]
    assert r.session_id == "s1"
    assert r.source == "copilot_cli"
    assert r.model == "claude-sonnet-4-6"
    assert r.turns == 3
    assert r.input_tokens == 1000
    assert r.cache_read_tokens == 500
    assert r.cache_creation_tokens == 100


def test_multi_model_shutdown_yields_separate_records(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _shutdown({
            "claude-opus-4-8": {"turns": 2, "input": 10000, "output": 500},
            "claude-sonnet-4-6": {"turns": 0, "input": 3000, "output": 100},
        }),
    ])
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))
    assert len(records) == 2
    models = {r.model for r in records}
    assert models == {"claude-opus-4-8", "claude-sonnet-4-6"}


def test_since_filter(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s-old", "/work/x", "", "2026-06-01T10:00:00.000Z", "2026-06-01T11:00:00.000Z")
    _write_events(home, "s-old", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))
    assert records == []


def test_project_from_repository(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/myrepo",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    r = list(CopilotCliCollector(home, track_project_names=True).collect(date(2026, 6, 10)))[0]
    assert r.project == "myrepo"


def test_project_from_cwd_when_no_repo(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/localproject", "",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    r = list(CopilotCliCollector(home, track_project_names=True).collect(date(2026, 6, 10)))[0]
    assert r.project == "localproject"


def test_project_none_when_disabled(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/secret", "owner/secret",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    r = list(CopilotCliCollector(home, track_project_names=False).collect(date(2026, 6, 10)))[0]
    assert r.project is None
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_cli_collector.py -v
```
Expected: failures (old collector returns `ActivityRecord`).

- [ ] **Step 3: Replace src/collectors/copilot_cli.py**

```python
# src/collectors/copilot_cli.py
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterator

from .base import to_date, to_local_iso
from ..models import SessionRecord, UNKNOWN_MODEL

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)")


class CopilotCliCollector:
    """Yields one SessionRecord per (Copilot session id, model)."""

    source = "copilot_cli"

    def __init__(self, copilot_home: Path, track_project_names: bool = False) -> None:
        self._home = copilot_home
        self._track_projects = track_project_names

    def collect(self, since: date) -> Iterator[SessionRecord]:
        db_path = self._home / "session-store.db"
        if not db_path.exists():
            return

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, cwd, repository, startedAt, endedAt FROM sessions"
            ).fetchall()
        except sqlite3.OperationalError:
            conn.close()
            return
        conn.close()

        for row in rows:
            session_id: str = row["id"]
            start_ts = row["startedAt"]
            end_ts = row["endedAt"]

            session_date = to_date(start_ts)
            if session_date is None or session_date < since:
                continue

            project: str | None = None
            if self._track_projects:
                repo: str = row["repository"] or ""
                cwd: str = row["cwd"] or ""
                if repo:
                    project = repo.split("/")[-1]
                elif cwd:
                    project = Path(cwd).name

            date_str = session_date.isoformat()
            start_iso = to_local_iso(start_ts)
            end_iso = to_local_iso(end_ts)

            yield from self._parse_events(
                session_id, date_str, start_iso, end_iso, project
            )

    def _parse_events(
        self,
        session_id: str,
        date_str: str,
        start_ts: str | None,
        end_ts: str | None,
        project: str | None,
    ) -> Iterator[SessionRecord]:
        events_path = self._home / "session-state" / session_id / "events.jsonl"
        if not events_path.exists():
            return

        # Try shutdown event first (has per-model breakdown)
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "session.shutdown":
                yield from self._from_shutdown(
                    event, session_id, date_str, start_ts, end_ts, project
                )
                return

        # Fall back to summing assistant messages
        yield self._from_assistant_events(
            events_path, session_id, date_str, start_ts, end_ts, project
        )

    def _from_shutdown(
        self, event: dict, session_id: str,
        date_str: str, start_ts: str | None, end_ts: str | None,
        project: str | None,
    ) -> Iterator[SessionRecord]:
        metrics: dict = event.get("modelMetrics") or {}
        for model, m in metrics.items():
            yield SessionRecord(
                session_id=session_id,
                source=self.source,
                model=model or UNKNOWN_MODEL,
                date=date_str,
                start_ts=start_ts,
                end_ts=end_ts,
                project=project,
                turns=m.get("turns", 0),
                input_tokens=m.get("inputTokens", 0),
                output_tokens=m.get("outputTokens", 0),
                cache_read_tokens=m.get("cacheReadTokens", 0),
                cache_creation_tokens=m.get("cacheWriteTokens", 0),
                reasoning_tokens=m.get("reasoningTokens", 0),
            )

    def _from_assistant_events(
        self, events_path: Path, session_id: str,
        date_str: str, start_ts: str | None, end_ts: str | None,
        project: str | None,
    ) -> SessionRecord:
        totals = dict(input_tokens=0, output_tokens=0, cache_read_tokens=0,
                      cache_creation_tokens=0, reasoning_tokens=0)
        turns = 0
        model = UNKNOWN_MODEL
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "assistant.message":
                continue
            turns += 1
            if model == UNKNOWN_MODEL and event.get("model"):
                model = event["model"]
            usage = event.get("usage") or {}
            totals["input_tokens"] += usage.get("inputTokens", 0)
            totals["output_tokens"] += usage.get("outputTokens", 0)
            totals["cache_read_tokens"] += usage.get("cacheReadTokens", 0)
            totals["cache_creation_tokens"] += usage.get("cacheWriteTokens", 0)
            totals["reasoning_tokens"] += usage.get("reasoningTokens", 0)
        return SessionRecord(
            session_id=session_id,
            source=self.source,
            model=model,
            date=date_str,
            start_ts=start_ts,
            end_ts=end_ts,
            project=project,
            turns=turns,
            **totals,
        )
```

- [ ] **Step 4: Run copilot tests**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_cli_collector.py -v
```
Expected: all 6 tests PASS

- [ ] **Step 5: Run full suite**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest -q
```
Expected: all tests pass except possibly test_pipeline.py — check and fix if needed (it may reference `ActivityRecord` directly; if so, update the import to `SessionRecord`).

- [ ] **Step 6: Fix test_pipeline.py if it fails**

If `test_pipeline.py` imports or constructs `ActivityRecord`, replace with `SessionRecord(session_id="s1", source="claude_cli")` and update imports.

- [ ] **Step 7: Commit**

```bash
cd /Users/bipin/repo/TokenTracer && git add src/collectors/copilot_cli.py tests/test_cli_collector.py && git commit -m "feat: rewrite CopilotCliCollector to yield SessionRecord per (session, model)"
```

---

### Task 6: Report — cache efficiency header, --by-project, --sessions

**Files:**
- Modify: `src/report.py`
- Modify: `tests/test_store_report.py` (add report tests)

**Interfaces:**
- Consumes: `sessions` table from Task 3
- Produces: `UsageReporter(db_path: Path)`
- Produces: `UsageReporter.report(period, models, by_project, sessions_view, as_json) -> str`
- Produces: cache efficiency header on all text output: `"Cache efficiency: XX% read from cache (~YY% cost saved)"`

- [ ] **Step 1: Add report tests to test_store_report.py**

Append these tests to the bottom of `tests/test_store_report.py`:

```python
# ── Report tests ─────────────────────────────────────────────────────────────

from src.report import UsageReporter


def _populate(db: Path) -> None:
    store = UsageStore(db)
    store.upsert([
        SessionRecord(
            session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
            date="2026-06-15", turns=3,
            input_tokens=1000, output_tokens=200,
            cache_creation_tokens=500, cache_read_tokens=4000,
        ),
        SessionRecord(
            session_id="s2", source="claude_cli", model="claude-sonnet-4-6",
            date="2026-06-16", turns=2,
            input_tokens=800, output_tokens=150,
            cache_creation_tokens=200, cache_read_tokens=2000,
            project="myapp",
        ),
        SessionRecord(
            session_id="s3", source="claude_cli", model="claude-sonnet-4-6",
            date="2026-05-30", turns=4,
            input_tokens=500, output_tokens=100,
            cache_creation_tokens=0, cache_read_tokens=0,
        ),
    ])


def test_report_day_returns_string(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day")
    assert isinstance(output, str)
    assert len(output) > 0


def test_report_includes_cache_efficiency_header(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day")
    assert "Cache efficiency" in output


def test_report_month(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="month")
    assert "2026-06" in output


def test_report_by_project_excludes_null_projects(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="month", by_project=True)
    assert "myapp" in output
    # s1 has no project — its tokens should not appear under a project name
    assert "s1" not in output


def test_report_by_project_shows_note_when_no_projects(tmp_db):
    store = UsageStore(tmp_db)
    store.upsert([_rec("s1", project=None)])
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day", by_project=True)
    assert "track_project_names" in output


def test_report_sessions_view(tmp_db):
    _populate(tmp_db)
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="month", sessions_view=True)
    # All three session IDs should appear (truncated to 8 chars)
    assert "s1" in output
    assert "s2" in output


def test_report_json(tmp_db):
    _populate(tmp_db)
    import json as _json
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day", as_json=True)
    data = _json.loads(output)
    assert "cache_efficiency" in data
    assert "rows" in data
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_store_report.py -v -k "report"
```
Expected: failures (old `report.py` uses `usage` table, missing `by_project`/`sessions_view` params).

- [ ] **Step 3: Replace src/report.py**

```python
# src/report.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_PERIOD_SQL = {
    "day":   "date",
    "month": "strftime('%Y-%m', date)",
    "year":  "strftime('%Y', date)",
}

_DATE_RANGE_SQL = {
    "day":   "date = date('now', 'localtime')",
    "month": "date >= date('now', 'start of month', 'localtime')",
    "year":  "date >= date('now', 'start of year', 'localtime')",
}


@dataclass(frozen=True)
class UsageReporter:
    db_path: Path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _cache_efficiency(self, conn: sqlite3.Connection) -> tuple[float, float]:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(cache_creation_tokens), 0),
                COALESCE(SUM(cache_read_tokens), 0)
            FROM sessions
        """).fetchone()
        total = row[0] + row[1] + row[2]
        if total == 0:
            return 0.0, 0.0
        hit_rate = row[2] / total
        # cache-read costs ~10% of regular input → ~90% saving on those tokens
        cost_saved = hit_rate * 0.9
        return hit_rate, cost_saved

    def report(
        self,
        period: str = "day",
        models: Sequence[str] | None = None,
        by_project: bool = False,
        sessions_view: bool = False,
        as_json: bool = False,
    ) -> str:
        if period not in _PERIOD_SQL:
            raise ValueError(f"period must be one of {list(_PERIOD_SQL)}")

        conn = self._connect()
        try:
            hit_rate, cost_saved = self._cache_efficiency(conn)
            date_filter = _DATE_RANGE_SQL[period]
            model_filter = ""
            params: list = []
            if models:
                placeholders = ",".join("?" * len(models))
                model_filter = f" AND model IN ({placeholders})"
                params.extend(models)

            if sessions_view:
                return self._render_sessions(
                    conn, date_filter, model_filter, params,
                    hit_rate, cost_saved, as_json,
                )
            if by_project:
                return self._render_by_project(
                    conn, date_filter, model_filter, params,
                    hit_rate, cost_saved, as_json,
                )
            return self._render_default(
                conn, period, date_filter, model_filter, params,
                hit_rate, cost_saved, as_json,
            )
        finally:
            conn.close()

    def _render_default(
        self, conn, period, date_filter, model_filter, params,
        hit_rate, cost_saved, as_json,
    ) -> str:
        period_expr = _PERIOD_SQL[period]
        rows = conn.execute(f"""
            SELECT
                {period_expr}                            AS period,
                source,
                model,
                SUM(turns)                               AS turns,
                SUM(input_tokens)                        AS input_tokens,
                SUM(output_tokens)                       AS output_tokens,
                SUM(cache_creation_tokens)               AS cache_creation_tokens,
                SUM(cache_read_tokens)                   AS cache_read_tokens
            FROM sessions
            WHERE {date_filter}{model_filter}
            GROUP BY {period_expr}, source, model
            ORDER BY period DESC, input_tokens DESC
        """, params).fetchall()

        if as_json:
            return json.dumps({
                "cache_efficiency": {
                    "hit_rate": round(hit_rate, 4),
                    "cost_saved_rate": round(cost_saved, 4),
                },
                "rows": [dict(r) for r in rows],
            }, indent=2)

        return self._format_table(
            hit_rate, cost_saved,
            headers=["Period", "Source", "Model", "Turns",
                     "Input", "Output", "CacheCreate", "CacheRead"],
            rows=[[r["period"], r["source"], r["model"], r["turns"],
                   r["input_tokens"], r["output_tokens"],
                   r["cache_creation_tokens"], r["cache_read_tokens"]]
                  for r in rows],
        )

    def _render_by_project(
        self, conn, date_filter, model_filter, params,
        hit_rate, cost_saved, as_json,
    ) -> str:
        rows = conn.execute(f"""
            SELECT
                project,
                date,
                model,
                SUM(turns)                AS turns,
                SUM(input_tokens)         AS input_tokens,
                SUM(output_tokens)        AS output_tokens,
                SUM(cache_read_tokens)    AS cache_read_tokens
            FROM sessions
            WHERE project IS NOT NULL
              AND {date_filter}{model_filter}
            GROUP BY project, date, model
            ORDER BY SUM(input_tokens + cache_read_tokens) DESC
        """, params).fetchall()

        if not rows:
            note = (
                "No project data found. Enable project tracking:\n"
                "  python3 tracker.py config set track_project_names true\n"
                "Then re-collect: python3 tracker.py collect --track-projects"
            )
            if as_json:
                return json.dumps({"note": note, "rows": []}, indent=2)
            return note

        if as_json:
            return json.dumps({
                "cache_efficiency": {"hit_rate": round(hit_rate, 4)},
                "rows": [dict(r) for r in rows],
            }, indent=2)

        return self._format_table(
            hit_rate, cost_saved,
            headers=["Project", "Date", "Model", "Turns",
                     "Input", "Output", "CacheRead"],
            rows=[[r["project"], r["date"], r["model"], r["turns"],
                   r["input_tokens"], r["output_tokens"], r["cache_read_tokens"]]
                  for r in rows],
        )

    def _render_sessions(
        self, conn, date_filter, model_filter, params,
        hit_rate, cost_saved, as_json,
    ) -> str:
        rows = conn.execute(f"""
            SELECT
                session_id,
                COALESCE(project, '—') AS project,
                date,
                start_ts,
                end_ts,
                model,
                turns,
                input_tokens + cache_creation_tokens + cache_read_tokens AS total_tokens,
                cache_read_tokens,
                input_tokens + cache_creation_tokens + cache_read_tokens AS denom
            FROM sessions
            WHERE {date_filter}{model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, params).fetchall()

        if as_json:
            return json.dumps({
                "cache_efficiency": {"hit_rate": round(hit_rate, 4)},
                "rows": [dict(r) for r in rows],
            }, indent=2)

        table_rows = []
        for r in rows:
            sid = r["session_id"][:8]
            denom = r["denom"] or 0
            cache_pct = f"{r['cache_read_tokens'] / denom:.0%}" if denom else "—"
            table_rows.append([
                sid, r["project"], r["date"],
                (r["start_ts"] or "")[:19], (r["end_ts"] or "")[:19],
                r["turns"], r["total_tokens"], cache_pct,
            ])

        return self._format_table(
            hit_rate, cost_saved,
            headers=["Session", "Project", "Date", "Start", "End",
                     "Turns", "Tokens", "CacheHit%"],
            rows=table_rows,
        )

    @staticmethod
    def _format_table(
        hit_rate: float,
        cost_saved: float,
        headers: list[str],
        rows: list[list],
    ) -> str:
        efficiency_line = (
            f"Cache efficiency: {hit_rate:.0%} read from cache "
            f"(~{cost_saved:.0%} cost saved)\n"
        )
        if not rows:
            return efficiency_line + "No data for this period.\n"

        str_rows = [[str(c) for c in row] for row in rows]
        widths = [max(len(h), max((len(r[i]) for r in str_rows), default=0))
                  for i, h in enumerate(headers)]
        sep = "  ".join("-" * w for w in widths)
        header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
        data_lines = [
            "  ".join(c.ljust(w) for c, w in zip(row, widths))
            for row in str_rows
        ]
        return efficiency_line + "\n".join([header_line, sep] + data_lines) + "\n"
```

- [ ] **Step 4: Run report tests**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest tests/test_store_report.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Run full suite**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest -q
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
cd /Users/bipin/repo/TokenTracer && git add src/report.py tests/test_store_report.py && git commit -m "feat: add cache efficiency header, --by-project and --sessions report views"
```

---

### Task 7: Wire tracker.py — new CLI flags, config loading, pipeline plumbing

**Files:**
- Modify: `tracker.py`
- Modify: `tests/test_pipeline.py` (fix any `ActivityRecord` references)

**Interfaces:**
- Consumes: `Config.load()` from Task 2, `ClaudeCliCollector(track_project_names=...)` from Task 4, `CopilotCliCollector(track_project_names=...)` from Task 5
- Produces: `tracker.py collect [--track-projects | --no-track-projects]`
- Produces: `tracker.py report [--by-project] [--sessions] [--period day|month|year] [--model M] [--json]`

- [ ] **Step 1: Check and fix test_pipeline.py**

```bash
cd /Users/bipin/repo/TokenTracer && grep -n "ActivityRecord" tests/test_pipeline.py
```

If any matches: replace `ActivityRecord` with `SessionRecord` and update constructors to include `session_id` and `source` positional args. For example:

```python
# Before:
ActivityRecord("2026-06-15", "claude_cli", turns=1, input_tokens=100)
# After:
SessionRecord(session_id="s1", source="claude_cli", date="2026-06-15", turns=1, input_tokens=100)
```

Update the import:
```python
# Before:
from src.models import ActivityRecord
# After:
from src.models import SessionRecord
```

- [ ] **Step 2: Run full suite to confirm it passes before touching tracker.py**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest -q
```
Expected: all tests pass.

- [ ] **Step 3: Replace tracker.py**

```python
# tracker.py
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from src.collectors import CopilotCliCollector, ClaudeCliCollector
from src.config import Config, write_toml_setting
from src.pipeline import TrackerPipeline
from src.report import UsageReporter
from src.store import UsageStore


def _build_pipeline(cfg: Config, track_project_names: bool) -> TrackerPipeline:
    paths = cfg.paths
    return (
        TrackerPipeline()
        .add(CopilotCliCollector(paths.copilot_home, track_project_names=track_project_names))
        .add(ClaudeCliCollector(paths.claude_projects, track_project_names=track_project_names))
    )


def cmd_collect(args) -> int:
    # Resolve track_project_names: CLI flag > toml > default
    if args.track_projects is True:
        track = True
    elif args.track_projects is False:
        track = False
    else:
        track = None  # not specified — use toml/default

    cfg = Config.load(**({"track_project_names": track} if track is not None else {}))
    cfg = Config(
        paths=cfg.paths,
        db_path=Path(args.db) if args.db else cfg.db_path,
        lookback_days=args.lookback,
        track_project_names=cfg.track_project_names,
    )

    since = date.today() - timedelta(days=cfg.lookback_days)
    pipeline = _build_pipeline(cfg, cfg.track_project_names)
    result = pipeline.since(since).store(UsageStore(cfg.db_path)).run()

    for err in result.errors:
        print(f"Warning: {err}", file=sys.stderr)

    print(
        f"Collected {result.records_written} session records "
        f"from {result.collectors_run} collectors "
        f"(since {since.isoformat()})"
    )
    return 0


def cmd_report(args) -> int:
    cfg = Config.load()
    db_path = Path(args.db) if args.db else cfg.db_path
    reporter = UsageReporter(db_path)
    output = reporter.report(
        period=args.period,
        models=args.model or None,
        by_project=args.by_project,
        sessions_view=args.sessions,
        as_json=args.json,
    )
    print(output, end="")
    return 0


def cmd_config_set(args) -> int:
    supported = {"track_project_names"}
    if args.key not in supported:
        print(
            f"Unknown config key: {args.key!r}. Supported: {', '.join(supported)}",
            file=sys.stderr,
        )
        return 1
    bool_val = args.value.lower() in ("1", "true", "yes")
    write_toml_setting(args.key, bool_val)
    print(f"Set {args.key} = {bool_val} in ~/.tokentracer.toml")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tracker", description="AI token tracker")
    parser.add_argument("--db", default=None, help="path to usage.db")
    sub = parser.add_subparsers(dest="cmd")

    # collect
    p_collect = sub.add_parser("collect", help="collect usage from local logs")
    p_collect.add_argument("--lookback", type=int, default=3,
                           help="days of history to collect (default: 3)")
    p_collect.set_defaults(track_projects=None)
    track_group = p_collect.add_mutually_exclusive_group()
    track_group.add_argument("--track-projects", dest="track_projects",
                             action="store_const", const=True,
                             help="store project names (override toml)")
    track_group.add_argument("--no-track-projects", dest="track_projects",
                             action="store_const", const=False,
                             help="suppress project names (override toml)")

    # report
    p_report = sub.add_parser("report", help="show usage report")
    p_report.add_argument("--period", choices=["day", "month", "year"], default="day")
    p_report.add_argument("--model", action="append", dest="model",
                          help="filter to model(s) (repeatable)")
    p_report.add_argument("--by-project", action="store_true",
                          help="group by project (requires project tracking enabled)")
    p_report.add_argument("--sessions", action="store_true",
                          help="show individual session rows")
    p_report.add_argument("--json", action="store_true",
                          help="output as JSON")

    # config
    p_config = sub.add_parser("config", help="manage configuration")
    config_sub = p_config.add_subparsers(dest="config_cmd")
    p_config_set = config_sub.add_parser("set", help="set a config value")
    p_config_set.add_argument("key")
    p_config_set.add_argument("value")

    return parser, p_config


def main() -> None:
    parser, p_config = _build_parser()
    args = parser.parse_args()

    if args.cmd == "collect":
        sys.exit(cmd_collect(args))
    elif args.cmd == "report":
        sys.exit(cmd_report(args))
    elif args.cmd == "config":
        if args.config_cmd == "set":
            sys.exit(cmd_config_set(args))
        else:
            p_config.print_help()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run full test suite**

```bash
cd /Users/bipin/repo/TokenTracer && python3 -m pytest -q
```
Expected: all tests pass

- [ ] **Step 5: Smoke-test the CLI end-to-end**

```bash
cd /Users/bipin/repo/TokenTracer && python3 tracker.py collect --lookback 3
python3 tracker.py report --period day
python3 tracker.py report --period month --sessions
python3 tracker.py report --period month --by-project
python3 tracker.py config set track_project_names true
python3 tracker.py collect --lookback 3 --track-projects
python3 tracker.py report --period month --by-project
```

Verify:
- `collect` prints `"Collected N session records from 2 collectors"`
- `report --period day` shows cache efficiency header line
- `report --by-project` without project data shows the enable-tracking note
- `report --by-project` after `--track-projects` collect shows project names

- [ ] **Step 6: Commit**

```bash
cd /Users/bipin/repo/TokenTracer && git add tracker.py tests/test_pipeline.py && git commit -m "feat: wire track-projects flag through CLI and pipeline"
```
