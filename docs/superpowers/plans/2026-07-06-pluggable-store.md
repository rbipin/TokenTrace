# Pluggable Store Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a `SessionStore` Protocol so TokenTracer can write session records to multiple backends (SQLite + opt-in remotes) simultaneously, with a `sync` command to retry failed remote pushes.

**Architecture:** A new `src/stores/` package defines the `SessionStore` Protocol and a refactored `SqliteStore`. Stores are discovered via Python entry points (`tokentracer.stores` group). `TrackerPipeline` is updated to accept multiple stores; SQLite always writes first and must succeed; remotes push in parallel with log-and-continue error handling. A `sync_log` SQLite table tracks which records have been pushed to each remote; `tokentracer sync` retries unsynced records.

**Tech Stack:** Python 3.11+ stdlib only (`importlib.metadata`, `sqlite3`, `threading`); `pytest` for tests; `pyproject.toml` entry points for store discovery.

## Global Constraints

- Python ≥ 3.11 (no backport shims needed — `tomllib`, `importlib.metadata` are stdlib)
- No new runtime dependencies — stdlib only
- All existing tests must continue to pass after each task
- `src/store.py` must stay importable as a shim (do not delete it)
- Run tests with: `python3 -m pytest -q`
- Merge key for sessions: `(session_id, source, model)` — never change this

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/stores/__init__.py` | Create | `SessionStore` Protocol |
| `src/stores/sqlite.py` | Create | `SqliteStore` — refactored from `store.py`, adds `sync_log` |
| `src/stores/registry.py` | Create | Entry-point discovery + class-path loading |
| `src/store.py` | Modify | Deprecated shim re-exporting `SqliteStore` as `UsageStore` |
| `src/config.py` | Modify | Parse `[stores.X]` TOML sections into `StoreConfig` dataclasses |
| `src/pipeline.py` | Modify | `.stores(*stores)`, parallel remote push, `stores_failed` in `RunResult` |
| `tracker.py` | Modify | Wire stores from config into pipeline; add `sync` subcommand |
| `pyproject.toml` | Modify | Register `sqlite` entry point under `tokentracer.stores` |
| `tests/test_stores_protocol.py` | Create | Protocol conformance + SqliteStore unit tests |
| `tests/test_stores_registry.py` | Create | Registry loading tests |
| `tests/test_pipeline_multi_store.py` | Create | Multi-store pipeline tests |
| `tests/test_sync_command.py` | Create | sync_log and sync command tests |

---

## Task 1: `SessionStore` Protocol + `src/stores/` package

**Files:**
- Create: `src/stores/__init__.py`
- Create: `tests/test_stores_protocol.py`

**Interfaces:**
- Produces: `SessionStore` Protocol with `name: str`, `upsert(list[SessionRecord]) -> int`, `close() -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stores_protocol.py
from __future__ import annotations
from datetime import date
from pathlib import Path
import pytest
from src.stores import SessionStore
from src.models import SessionRecord


class _MinimalStore:
    name = "minimal"

    def upsert(self, records: list[SessionRecord]) -> int:
        return len(records)

    def close(self) -> None:
        pass


def test_minimal_store_satisfies_protocol():
    store: SessionStore = _MinimalStore()  # type: ignore[assignment]
    assert store.name == "minimal"
    assert store.upsert([]) == 0
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_stores_protocol.py -v
```
Expected: `ImportError: cannot import name 'SessionStore' from 'src.stores'`

- [ ] **Step 3: Create `src/stores/__init__.py`**

```python
"""Pluggable store interface for TokenTracer."""
from __future__ import annotations

from typing import Protocol

from ..models import SessionRecord


class SessionStore(Protocol):
    """Write-only sink for session records.

    Implementers must be safe to call from multiple threads (one call at a time).
    """

    name: str

    def upsert(self, records: list[SessionRecord]) -> int:
        """Persist records; return the count written."""
        ...

    def close(self) -> None:
        """Flush buffers and release resources."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_stores_protocol.py -v
```
Expected: PASS

- [ ] **Step 5: Run full suite to check no regressions**

```bash
python3 -m pytest -q
```
Expected: all existing tests pass

- [ ] **Step 6: Commit**

```bash
git add src/stores/__init__.py tests/test_stores_protocol.py
git commit -m "feat: add SessionStore Protocol"
```

---

## Task 2: `SqliteStore` + `sync_log` table

Refactor `UsageStore` from `src/store.py` into `src/stores/sqlite.py`, adding `sync_log` tracking and a deprecated shim so existing imports still work.

**Files:**
- Create: `src/stores/sqlite.py`
- Modify: `src/store.py` (shim only)
- Modify: `tests/test_stores_protocol.py` (add SqliteStore tests)
- Reference existing tests: `tests/test_store_report.py` (must still pass unchanged)

**Interfaces:**
- Consumes: `SessionStore` Protocol from Task 1
- Produces:
  - `SqliteStore(db_path: Path)` — satisfies `SessionStore`, `name = "sqlite"`
  - `SqliteStore.unsynced_for(store_name: str) -> list[SessionRecord]`
  - `SqliteStore.mark_synced(records: list[SessionRecord], store_name: str) -> None`
  - `UsageStore` in `src/store.py` = alias for `SqliteStore` (deprecated)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_stores_protocol.py`:

```python
from src.stores.sqlite import SqliteStore


def _rec(session_id: str, **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-sonnet-4-6",
                    date="2026-07-01")
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def test_sqlite_store_satisfies_protocol(tmp_path):
    store = SqliteStore(tmp_path / "usage.db")
    assert store.name == "sqlite"
    n = store.upsert([_rec("s1"), _rec("s2")])
    assert n == 2
    store.close()


def test_sqlite_store_unsynced_for(tmp_path):
    db = tmp_path / "usage.db"
    store = SqliteStore(db)
    store.upsert([_rec("s1"), _rec("s2")])
    pending = store.unsynced_for("supabase")
    assert len(pending) == 2
    store.mark_synced([pending[0]], "supabase")
    pending2 = store.unsynced_for("supabase")
    assert len(pending2) == 1
    assert pending2[0].session_id == "s2"


def test_sqlite_store_mark_synced_idempotent(tmp_path):
    db = tmp_path / "usage.db"
    store = SqliteStore(db)
    store.upsert([_rec("s1")])
    rec = store.unsynced_for("cosmos")[0]
    store.mark_synced([rec], "cosmos")
    store.mark_synced([rec], "cosmos")  # second call must not raise
    assert store.unsynced_for("cosmos") == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_stores_protocol.py -v
```
Expected: `ImportError: cannot import name 'SqliteStore' from 'src.stores.sqlite'`

- [ ] **Step 3: Create `src/stores/sqlite.py`**

```python
"""SQLite-backed SessionStore (the always-on local store)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from ..models import SessionRecord

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

_CREATE_SYNC_LOG = """
CREATE TABLE IF NOT EXISTS sync_log (
    session_id  TEXT NOT NULL,
    source      TEXT NOT NULL,
    model       TEXT NOT NULL,
    store_name  TEXT NOT NULL,
    synced_at   TEXT NOT NULL,
    PRIMARY KEY (session_id, source, model, store_name)
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

_UNSYNCED = """
SELECT s.session_id, s.source, s.model, s.date, s.start_ts, s.end_ts,
       s.project, s.turns, s.tool_calls, s.input_tokens, s.output_tokens,
       s.cache_creation_tokens, s.cache_read_tokens,
       s.context_peak_tokens, s.reasoning_tokens
FROM sessions s
LEFT JOIN sync_log l
    ON s.session_id = l.session_id
    AND s.source = l.source
    AND s.model = l.model
    AND l.store_name = ?
WHERE l.session_id IS NULL
"""

_MARK_SYNCED = """
INSERT OR IGNORE INTO sync_log (session_id, source, model, store_name, synced_at)
VALUES (?, ?, ?, ?, datetime('now'))
"""


class SqliteStore:
    name = "sqlite"

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
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
            conn.execute(_CREATE_SYNC_LOG)
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

    def unsynced_for(self, store_name: str) -> list[SessionRecord]:
        """Return all records not yet synced to the given store."""
        with self._connect() as conn:
            rows = conn.execute(_UNSYNCED, (store_name,)).fetchall()
        return [
            SessionRecord(
                session_id=row[0], source=row[1], model=row[2], date=row[3],
                start_ts=row[4], end_ts=row[5], project=row[6],
                turns=row[7], tool_calls=row[8], input_tokens=row[9],
                output_tokens=row[10], cache_creation_tokens=row[11],
                cache_read_tokens=row[12], context_peak_tokens=row[13],
                reasoning_tokens=row[14],
            )
            for row in rows
        ]

    def mark_synced(self, records: list[SessionRecord], store_name: str) -> None:
        """Record that the given records were successfully pushed to store_name."""
        if not records:
            return
        with self._connect() as conn:
            conn.executemany(
                _MARK_SYNCED,
                [(r.session_id, r.source, r.model, store_name) for r in records],
            )

    def close(self) -> None:
        pass  # connections are opened/closed per operation
```

- [ ] **Step 4: Update `src/store.py` to a deprecated shim**

Replace the entire file content with:

```python
"""Deprecated: import from src.stores.sqlite instead."""
from __future__ import annotations
import warnings
from .stores.sqlite import SqliteStore as UsageStore  # noqa: F401

warnings.warn(
    "src.store.UsageStore is deprecated; use src.stores.sqlite.SqliteStore",
    DeprecationWarning,
    stacklevel=2,
)
```

- [ ] **Step 5: Run all tests**

```bash
python3 -m pytest -q
```
Expected: all pass (existing `test_store_report.py` imports `UsageStore` from `src.store` — the shim keeps that working; deprecation warning is expected)

- [ ] **Step 6: Commit**

```bash
git add src/stores/sqlite.py src/store.py tests/test_stores_protocol.py
git commit -m "feat: add SqliteStore with sync_log; shim src/store.py"
```

---

## Task 3: Store registry

**Files:**
- Create: `src/stores/registry.py`
- Modify: `pyproject.toml`
- Create: `tests/test_stores_registry.py`

**Interfaces:**
- Consumes: `SessionStore` Protocol from Task 1; `SqliteStore` from Task 2
- Produces:
  - `load_store_registry() -> dict[str, type]` — entry-point name → class
  - `instantiate_store(name: str, params: dict, class_path: str | None = None) -> SessionStore`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stores_registry.py
from __future__ import annotations
import pytest
from src.stores.registry import load_store_registry, instantiate_store
from src.stores.sqlite import SqliteStore


def test_sqlite_in_registry():
    registry = load_store_registry()
    assert "sqlite" in registry
    assert registry["sqlite"] is SqliteStore


def test_instantiate_by_name(tmp_path):
    store = instantiate_store("sqlite", {"db_path": tmp_path / "usage.db"})
    assert store.name == "sqlite"
    store.close()


def test_instantiate_by_class_path(tmp_path):
    store = instantiate_store(
        "sqlite",
        {"db_path": tmp_path / "usage.db"},
        class_path="src.stores.sqlite.SqliteStore",
    )
    assert store.name == "sqlite"
    store.close()


def test_unknown_store_raises():
    with pytest.raises(ValueError, match="Unknown store"):
        instantiate_store("nonexistent", {})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_stores_registry.py -v
```
Expected: `ImportError: cannot import name 'load_store_registry'`

- [ ] **Step 3: Create `src/stores/registry.py`**

```python
"""Store discovery via Python entry points."""
from __future__ import annotations

import importlib
from importlib.metadata import entry_points

from . import SessionStore

_GROUP = "tokentracer.stores"


def load_store_registry() -> dict[str, type]:
    """Return mapping of entry-point name → store class."""
    return {ep.name: ep.load() for ep in entry_points(group=_GROUP)}


def instantiate_store(
    name: str,
    params: dict,
    class_path: str | None = None,
) -> SessionStore:
    """Load and instantiate a store by name or fully-qualified class path.

    class_path bypasses the registry entirely (power-user escape hatch).
    params are passed as keyword arguments to the store constructor.
    """
    if class_path:
        module_name, cls_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        cls = getattr(module, cls_name)
    else:
        registry = load_store_registry()
        if name not in registry:
            raise ValueError(
                f"Unknown store: {name!r}. "
                f"Install a package providing a '{_GROUP}' entry point for it, "
                f"or use class = \"module.ClassName\" in your config."
            )
        cls = registry[name]
    return cls(**params)
```

- [ ] **Step 4: Register the sqlite entry point in `pyproject.toml`**

Add after the `[project.scripts]` section:

```toml
[project.entry-points."tokentracer.stores"]
sqlite = "src.stores.sqlite:SqliteStore"
```

- [ ] **Step 5: Install package so entry point is discoverable**

```bash
pip install -e . --quiet
```

- [ ] **Step 6: Run all tests**

```bash
python3 -m pytest -q
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/stores/registry.py pyproject.toml tests/test_stores_registry.py
git commit -m "feat: add store registry via entry points"
```

---

## Task 4: Config `[stores]` parsing

**Files:**
- Modify: `src/config.py`
- Create: `tests/test_config_stores.py`

**Interfaces:**
- Produces:
  - `StoreConfig(name: str, class_path: str | None, params: dict)`
  - `Config.remote_stores: tuple[StoreConfig, ...]` — parsed from `[stores.X]` TOML sections; excludes `sqlite`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_stores.py
from __future__ import annotations
import textwrap
from pathlib import Path
import pytest
from src.config import Config, StoreConfig


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".tokentracer.toml"
    p.write_text(textwrap.dedent(content))
    return p


def test_no_stores_section(tmp_path, monkeypatch):
    toml = _write_toml(tmp_path, "[tracking]\ntrack_project_names = false\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.remote_stores == ()


def test_stores_parsed(tmp_path, monkeypatch):
    toml = _write_toml(tmp_path, """
        [stores.supabase]
        url = "https://example.supabase.co"
        api_key = "secret"

        [stores.mystore]
        class = "mypackage.MyStore"
        endpoint = "https://internal"
    """)
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert len(cfg.remote_stores) == 2

    sup = next(s for s in cfg.remote_stores if s.name == "supabase")
    assert sup.class_path is None
    assert sup.params == {"url": "https://example.supabase.co", "api_key": "secret"}

    my = next(s for s in cfg.remote_stores if s.name == "mystore")
    assert my.class_path == "mypackage.MyStore"
    assert my.params == {"endpoint": "https://internal"}


def test_sqlite_section_excluded(tmp_path, monkeypatch):
    toml = _write_toml(tmp_path, "[stores.sqlite]\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.remote_stores == ()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_config_stores.py -v
```
Expected: `AttributeError: type object 'Config' has no attribute 'remote_stores'`

- [ ] **Step 3: Add `StoreConfig` and update `Config` in `src/config.py`**

Add `StoreConfig` dataclass after the imports block:

```python
@dataclass(frozen=True)
class StoreConfig:
    name: str
    class_path: str | None  # from "class" key; None means resolve via entry points
    params: dict             # remaining keys passed as kwargs to the constructor
```

Add `remote_stores` field to `Config`:

```python
@dataclass(frozen=True)
class Config:
    paths: Paths = field(default_factory=Paths)
    db_path: Path = field(
        default_factory=lambda: Path.home() / ".tokentracer" / "usage.db"
    )
    lookback_days: int = 3
    track_project_names: bool = False
    remote_stores: tuple[StoreConfig, ...] = field(default_factory=tuple)
```

Update `Config.load()` to parse `[stores]`. Replace the existing `load` method body:

```python
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
                stores_raw = data.get("stores", {})
                remote: list[StoreConfig] = []
                for store_name, store_vals in stores_raw.items():
                    if store_name == "sqlite":
                        continue  # sqlite is always built-in; ignore explicit section
                    class_path = store_vals.pop("class", None) if isinstance(store_vals, dict) else None
                    params = dict(store_vals) if isinstance(store_vals, dict) else {}
                    remote.append(StoreConfig(name=store_name, class_path=class_path, params=params))
                base["remote_stores"] = tuple(remote)
            except Exception as exc:
                print(f"Warning: could not parse ~/.tokentracer.toml: {exc}", file=sys.stderr)
        base.update(overrides)
        return cls(**base)
```

- [ ] **Step 4: Run all tests**

```bash
python3 -m pytest -q
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config_stores.py
git commit -m "feat: parse [stores.X] config sections into StoreConfig"
```

---

## Task 5: Pipeline multi-store support

**Files:**
- Modify: `src/pipeline.py`
- Modify: `tracker.py` (wire stores from config into pipeline)
- Create: `tests/test_pipeline_multi_store.py`
- Reference: `tests/test_pipeline.py` (must still pass unchanged)

**Interfaces:**
- Consumes: `SessionStore` Protocol (Task 1); `StoreConfig` (Task 4); `instantiate_store` (Task 3)
- Produces:
  - `TrackerPipeline.stores(*stores: SessionStore) -> TrackerPipeline`
  - `RunResult.stores_failed: list[str]`
  - `.store(store)` remains as deprecated alias

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline_multi_store.py
from __future__ import annotations
from datetime import date
import pytest
from src.models import SessionRecord
from src.pipeline import TrackerPipeline
from src.stores.sqlite import SqliteStore


class _StubStore:
    def __init__(self, name, boom=False):
        self.name = name
        self._boom = boom
        self.received: list[SessionRecord] = []

    def upsert(self, records):
        if self._boom:
            raise RuntimeError("remote down")
        self.received.extend(records)
        return len(records)

    def close(self):
        pass


class _StubCollector:
    def __init__(self, source, records):
        self.source = source
        self._records = records

    def collect(self, since):
        return self._records


def _rec(sid):
    return SessionRecord(session_id=sid, source="claude_cli",
                         model="claude-sonnet-4-6", date="2026-07-01", turns=1)


def test_multi_store_all_succeed(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    remote = _StubStore("remote_a")
    col = _StubCollector("claude_cli", [_rec("s1")])
    result = (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, remote)
        .run()
    )
    assert result.records_written == 1
    assert result.stores_failed == []
    assert len(remote.received) == 1


def test_remote_failure_does_not_abort(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    bad_remote = _StubStore("remote_bad", boom=True)
    col = _StubCollector("claude_cli", [_rec("s1")])
    result = (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, bad_remote)
        .run()
    )
    assert result.records_written == 1          # sqlite succeeded
    assert len(result.stores_failed) == 1       # remote failure captured
    assert "remote_bad" in result.stores_failed[0]


def test_store_alias_still_works(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    col = _StubCollector("claude_cli", [_rec("s1")])
    result = (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .store(sqlite)          # deprecated alias
        .run()
    )
    assert result.records_written == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_pipeline_multi_store.py -v
```
Expected: `AttributeError: 'RunResult' object has no attribute 'stores_failed'`

- [ ] **Step 3: Update `src/pipeline.py`**

Replace the entire file:

```python
"""Fluent pipeline wiring collectors to stores."""
from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import List

from .collectors.base import ActivityCollector
from .models import SessionRecord, merge_records
from .stores import SessionStore


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single pipeline run."""

    records_written: int
    collectors_run: int
    errors: List[str] = field(default_factory=list)
    stores_failed: List[str] = field(default_factory=list)


class TrackerPipeline:
    """Builds and runs a collection pass.

    Usage::

        (TrackerPipeline()
            .add(ClaudeCliCollector(...))
            .since(start)
            .stores(SqliteStore(db), remote_store)
            .run())
    """

    def __init__(self) -> None:
        self._collectors: list[ActivityCollector] = []
        self._since: date | None = None
        self._stores: list[SessionStore] = []

    def add(self, collector: ActivityCollector) -> "TrackerPipeline":
        self._collectors.append(collector)
        return self

    def since(self, start: date) -> "TrackerPipeline":
        self._since = start
        return self

    def stores(self, *stores: SessionStore) -> "TrackerPipeline":
        self._stores = list(stores)
        return self

    def store(self, store: SessionStore) -> "TrackerPipeline":
        warnings.warn(
            "TrackerPipeline.store() is deprecated; use .stores()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.stores(store)

    def run(self) -> RunResult:
        if self._since is None:
            raise ValueError("since(start) must be set before run()")
        if not self._stores:
            raise ValueError("stores(...) must be set before run()")

        records: list[SessionRecord] = []
        errors: list[str] = []

        def _collect(collector: ActivityCollector) -> tuple[list[SessionRecord], str | None]:
            try:
                return list(collector.collect(self._since)), None
            except Exception as exc:
                name = getattr(collector, "source", type(collector).__name__)
                return [], f"{name}: {exc}"

        with ThreadPoolExecutor(max_workers=max(len(self._collectors), 1)) as pool:
            futures = {pool.submit(_collect, c): c for c in self._collectors}
            for future in as_completed(futures):
                recs, err = future.result()
                records.extend(recs)
                if err:
                    errors.append(err)

        merged = merge_records(records)

        # SQLite (first store) must succeed
        written = self._stores[0].upsert(merged)
        self._stores[0].close()

        # Remotes: parallel, log-and-continue
        stores_failed: list[str] = []

        def _push(store: SessionStore) -> str | None:
            try:
                store.upsert(merged)
                store.close()
                return None
            except Exception as exc:
                return f"{store.name}: {exc}"

        if len(self._stores) > 1:
            with ThreadPoolExecutor(max_workers=len(self._stores) - 1) as pool:
                for err in pool.map(_push, self._stores[1:]):
                    if err:
                        stores_failed.append(err)

        return RunResult(
            records_written=written,
            collectors_run=len(self._collectors),
            errors=errors,
            stores_failed=stores_failed,
        )
```

- [ ] **Step 4: Update `tracker.py` `cmd_collect` to wire stores from config**

Replace the entire imports block at the top of `tracker.py` with:

```python
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from src.collectors import CopilotCliCollector, ClaudeCliCollector
from src.config import Config, write_toml_setting
from src.pipeline import TrackerPipeline
from src.report import UsageReporter
from src.stores.sqlite import SqliteStore
from src.stores.registry import instantiate_store
```

Then replace `_build_pipeline` and update `cmd_collect`:

```python
def _build_pipeline(cfg: Config, track_project_names: bool) -> TrackerPipeline:
    paths = cfg.paths
    return (
        TrackerPipeline()
        .add(CopilotCliCollector(paths.copilot_home, track_project_names=track_project_names))
        .add(ClaudeCliCollector(paths.claude_projects, track_project_names=track_project_names))
    )


def _build_stores(cfg: Config) -> list:
    stores = [SqliteStore(cfg.db_path)]
    for sc in cfg.remote_stores:
        try:
            stores.append(instantiate_store(sc.name, sc.params, sc.class_path))
        except Exception as exc:
            print(f"Warning: could not load store {sc.name!r}: {exc}", file=sys.stderr)
    return stores


def cmd_collect(args) -> int:
    if args.track_projects is True:
        track = True
    elif args.track_projects is False:
        track = False
    else:
        track = None

    cfg = Config.load(**({"track_project_names": track} if track is not None else {}))
    cfg = Config(
        paths=cfg.paths,
        db_path=Path(args.db) if args.db else cfg.db_path,
        lookback_days=args.lookback,
        track_project_names=cfg.track_project_names,
        remote_stores=cfg.remote_stores,
    )

    since = date.today() - timedelta(days=cfg.lookback_days)
    pipeline = _build_pipeline(cfg, cfg.track_project_names)
    stores = _build_stores(cfg)
    result = pipeline.since(since).stores(*stores).run()

    for err in result.errors:
        print(f"Warning: {err}", file=sys.stderr)
    for err in result.stores_failed:
        print(f"Warning [store]: {err}", file=sys.stderr)

    print(
        f"Collected {result.records_written} session records "
        f"from {result.collectors_run} collectors "
        f"(since {since.isoformat()})"
    )
    return 0
```

Also remove the old `from src.store import UsageStore` import and the old `pipeline.since(since).store(UsageStore(cfg.db_path)).run()` line (replaced above).

- [ ] **Step 5: Run all tests**

```bash
python3 -m pytest -q
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/pipeline.py tracker.py tests/test_pipeline_multi_store.py
git commit -m "feat: multi-store pipeline with parallel remote push"
```

---

## Task 6: `sync` command

**Files:**
- Modify: `tracker.py`
- Create: `tests/test_sync_command.py`

**Interfaces:**
- Consumes: `SqliteStore.unsynced_for` / `mark_synced` (Task 2); `instantiate_store` (Task 3); `Config.remote_stores` (Task 4)
- Produces: `tokentracer sync [--dry-run]` CLI command

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sync_command.py
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

    from tracker import _run_sync
    result = _run_sync(sqlite, [remote], dry_run=False)

    assert result == {"supabase": {"pushed": 2, "failed": False}}
    assert len(remote.pushed) == 2
    assert sqlite.unsynced_for("supabase") == []


def test_sync_dry_run_does_not_push(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1")
    remote = _StubRemote("supabase")

    from tracker import _run_sync
    result = _run_sync(sqlite, [remote], dry_run=True)

    assert result == {"supabase": {"pending": 1}}
    assert remote.pushed == []
    assert len(sqlite.unsynced_for("supabase")) == 1


def test_sync_remote_failure_leaves_unsynced(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1")
    bad_remote = _StubRemote("cosmos", boom=True)

    from tracker import _run_sync
    result = _run_sync(sqlite, [bad_remote], dry_run=False)

    assert result["cosmos"]["failed"] is True
    assert len(sqlite.unsynced_for("cosmos")) == 1


def test_sync_already_synced_not_pushed_again(tmp_path):
    db = tmp_path / "usage.db"
    sqlite = _seed(db, "s1", "s2")
    remote = _StubRemote("supabase")
    # Pre-mark s1 as synced
    sqlite.mark_synced([_rec("s1")], "supabase")

    from tracker import _run_sync
    _run_sync(sqlite, [remote], dry_run=False)

    assert len(remote.pushed) == 1
    assert remote.pushed[0].session_id == "s2"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_sync_command.py -v
```
Expected: `ImportError: cannot import name '_run_sync' from 'tracker'`

- [ ] **Step 3: Add `_run_sync` and `cmd_sync` to `tracker.py`**

Add these functions after `cmd_collect`:

```python
def _run_sync(
    sqlite_store,
    remote_stores: list,
    dry_run: bool,
) -> dict:
    """Core sync logic — separated for testability.

    Returns a dict: {store_name: {"pushed": N, "failed": bool} | {"pending": N}}
    """
    result = {}
    for store in remote_stores:
        pending = sqlite_store.unsynced_for(store.name)
        if dry_run:
            result[store.name] = {"pending": len(pending)}
            continue
        try:
            if pending:
                store.upsert(pending)
                store.close()
                sqlite_store.mark_synced(pending, store.name)
            result[store.name] = {"pushed": len(pending), "failed": False}
        except Exception as exc:
            print(f"Warning [{store.name}]: {exc}", file=sys.stderr)
            result[store.name] = {"pushed": 0, "failed": True, "error": str(exc)}
    return result


def cmd_sync(args) -> int:
    cfg = Config.load()
    db_path = Path(args.db) if args.db else cfg.db_path

    if not cfg.remote_stores:
        print("No remote stores configured. Add [stores.X] sections to ~/.tokentracer.toml")
        return 0

    from src.stores.sqlite import SqliteStore
    from src.stores.registry import instantiate_store

    sqlite_store = SqliteStore(db_path)
    remote_stores = []
    for sc in cfg.remote_stores:
        try:
            remote_stores.append(instantiate_store(sc.name, sc.params, sc.class_path))
        except Exception as exc:
            print(f"Warning: could not load store {sc.name!r}: {exc}", file=sys.stderr)

    if not remote_stores:
        print("No remote stores could be loaded.")
        return 1

    label = "(dry run) " if args.dry_run else ""
    print(f"Syncing {len(remote_stores)} store(s)... {label}")
    result = _run_sync(sqlite_store, remote_stores, dry_run=args.dry_run)

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

- [ ] **Step 4: Register the `sync` subcommand in `_build_parser`**

In the `_build_parser` function, add before `return parser, p_config`:

```python
    # sync
    p_sync = sub.add_parser("sync", help="push unsynced records to remote stores")
    p_sync.add_argument("--dry-run", action="store_true",
                        help="show pending counts without pushing")
```

Update `main()` to handle the new subcommand:

```python
    elif args.cmd == "sync":
        sys.exit(cmd_sync(args))
```

Add this elif after the `elif args.cmd == "report":` block.

- [ ] **Step 5: Run all tests**

```bash
python3 -m pytest -q
```
Expected: all pass

- [ ] **Step 6: Smoke-test the CLI**

```bash
python3 tracker.py sync --dry-run
```
Expected output: `No remote stores configured. Add [stores.X] sections to ~/.tokentracer.toml`

- [ ] **Step 7: Commit**

```bash
git add tracker.py tests/test_sync_command.py
git commit -m "feat: add tokentracer sync command with dry-run support"
```

---

## Self-Review Checklist

- [x] **SessionStore Protocol** — Task 1
- [x] **SqliteStore satisfies Protocol** — Task 2
- [x] **sync_log table + unsynced_for + mark_synced** — Task 2
- [x] **src/store.py shim** — Task 2
- [x] **Entry-point registry** — Task 3
- [x] **pyproject.toml entry point registration** — Task 3
- [x] **[stores.X] config parsing + StoreConfig** — Task 4
- [x] **sqlite excluded from remote_stores** — Task 4
- [x] **Pipeline .stores() + stores_failed** — Task 5
- [x] **Deprecated .store() alias** — Task 5
- [x] **tracker.py wires stores from config** — Task 5
- [x] **sync command + dry-run** — Task 6
- [x] **Post-implementation: update README + CLAUDE.md** — reminder in design doc
