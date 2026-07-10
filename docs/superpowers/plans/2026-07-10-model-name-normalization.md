# Model Name Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize raw model identifiers (e.g. `claude-haiku-4-5-20251001` vs `claude-haiku-4-5`, or Copilot's own name for a Claude model) into a stable `canonical_model` used for reporting, via a pluggable Pipes-and-Filters middleware stage in `TrackerPipeline`, with backfill support for historical rows.

**Architecture:** A pure `normalize_model(raw, source)` function (regex date-suffix strip → static TOML lookup → passthrough) is wrapped in a `ModelNormalizeMiddleware` implementing a new `RecordMiddleware` protocol. `TrackerPipeline.run()` threads the merged batch through all registered middlewares between `merge_records()` and the sqlite upsert. `SessionRecord` gains a `canonical_model` field; `SqliteStore` persists it alongside the raw `model` and clears stale `sync_log` entries whenever any field changes on upsert, so `collect --lookback N` + `sync` naturally backfills and re-pushes normalized data. `report.py`'s grouping/filtering switches to `COALESCE(canonical_model, model)`.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, `tomllib`, `dataclasses`, `pytest`.

## Global Constraints

- Python 3.11+ only (per README) — use `tomllib` directly, no version-fallback shims.
- `SessionRecord` is a frozen dataclass — use `dataclasses.replace()` to produce modified copies, never mutate in place.
- Follow existing style: plain `typing.Protocol` for interfaces, no default-method base classes (matches `ActivityCollector`, `SessionStore`).
- Existing dedup primary key `(session_id, source, model)` is unchanged — `canonical_model` is additive, never part of any key.
- Every new/changed test must pass alongside the full existing suite (`pytest -q` from repo root).

---

### Task 1: `normalize_model()` function and cross-vendor lookup table

**Files:**
- Create: `src/model_normalize.py`
- Create: `src/model_aliases.toml`
- Test: `tests/test_model_normalize.py`

**Interfaces:**
- Produces: `normalize_model(raw: str, source: str) -> str` — used by Task 4's `ModelNormalizeMiddleware`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_normalize.py`:

```python
from __future__ import annotations

from src.model_normalize import normalize_model


def test_strips_date_suffix_from_claude_snapshot():
    assert normalize_model("claude-haiku-4-5-20251001", "claude_cli") == "claude-haiku-4-5"


def test_passes_through_alias_form_unchanged():
    assert normalize_model("claude-sonnet-4-6", "claude_cli") == "claude-sonnet-4-6"


def test_looks_up_cross_vendor_name():
    assert normalize_model("claude-sonnet-4.5", "copilot_cli") == "claude-sonnet-4-5"


def test_passes_through_unrecognized_name():
    assert normalize_model("gpt-4o", "copilot_cli") == "gpt-4o"


def test_passes_through_unknown_sentinel():
    assert normalize_model("unknown", "claude_cli") == "unknown"


def test_passes_through_synthetic_sentinel():
    assert normalize_model("<synthetic>", "claude_cli") == "<synthetic>"


def test_regex_does_not_misfire_on_non_date_suffix():
    assert normalize_model("o1-preview", "copilot_cli") == "o1-preview"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.model_normalize'`

- [ ] **Step 3: Write the lookup table**

Create `src/model_aliases.toml`:

```toml
# Cross-vendor model name reconciliation.
# Keyed by collector source, then the raw string that source reports,
# mapping to the canonical name used across all sources.
[copilot_cli]
"claude-sonnet-4.5" = "claude-sonnet-4-5"
```

- [ ] **Step 4: Write the implementation**

Create `src/model_normalize.py`:

```python
"""Normalize raw model identifiers into a stable canonical form.

Order of resolution:
1. Strip a trailing -YYYYMMDD date suffix (Anthropic's snapshot convention).
2. Look up (source, raw) in the static cross-vendor alias table.
3. Pass through the raw string unchanged.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

_DATE_SUFFIX_RE = re.compile(r"^(.+)-(\d{8})$")
_ALIASES_PATH = Path(__file__).parent / "model_aliases.toml"


def _load_aliases() -> dict[str, dict[str, str]]:
    if not _ALIASES_PATH.exists():
        return {}
    with open(_ALIASES_PATH, "rb") as fh:
        return tomllib.load(fh)


_ALIASES = _load_aliases()


def normalize_model(raw: str, source: str) -> str:
    """Normalize a raw model string reported by `source` into a canonical form."""
    match = _DATE_SUFFIX_RE.match(raw)
    if match:
        return match.group(1)
    return _ALIASES.get(source, {}).get(raw, raw)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_model_normalize.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add src/model_normalize.py src/model_aliases.toml tests/test_model_normalize.py
git commit -m "feat: add normalize_model with regex + lookup-table resolution"
```

---

### Task 2: `SessionRecord.canonical_model` field and SQLite schema

**Files:**
- Modify: `src/models.py`
- Modify: `src/stores/sqlite.py`
- Test: `tests/test_sqlite_canonical_model.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SessionRecord.canonical_model: str | None = None`; `sessions.canonical_model` column persisted/read by `SqliteStore.upsert()` / `unsynced_for()`. Later tasks (4, 5, 7) read/write this field.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sqlite_canonical_model.py`:

```python
from __future__ import annotations

import sqlite3

from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


def _rec(session_id: str, **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-haiku-4-5-20251001", date="2026-07-01")
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def test_canonical_model_defaults_to_none():
    rec = _rec("s1")
    assert rec.canonical_model is None


def test_canonical_model_persisted_and_read_back(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", canonical_model="claude-haiku-4-5")])
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT canonical_model FROM sessions WHERE session_id='s1'"
    ).fetchone()
    assert row[0] == "claude-haiku-4-5"


def test_canonical_model_null_when_not_set(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1")])
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT canonical_model FROM sessions WHERE session_id='s1'"
    ).fetchone()
    assert row[0] is None


def test_unsynced_for_round_trips_canonical_model(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", canonical_model="claude-haiku-4-5")])
    [rec] = store.unsynced_for("supabase")
    assert rec.canonical_model == "claude-haiku-4-5"


def test_migration_adds_canonical_model_to_existing_db(tmp_db):
    # Simulate a pre-existing DB created before this column existed.
    conn = sqlite3.connect(tmp_db)
    conn.execute("""
        CREATE TABLE sessions (
            session_id TEXT NOT NULL, source TEXT NOT NULL, model TEXT NOT NULL,
            date TEXT NOT NULL, start_ts TEXT, end_ts TEXT, project TEXT,
            turns INTEGER DEFAULT 0, tool_calls INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
            context_peak_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
            context TEXT DEFAULT 'personal',
            PRIMARY KEY (session_id, source, model)
        )
    """)
    conn.commit()
    conn.close()

    SqliteStore(tmp_db)  # triggers _migrate()

    conn2 = sqlite3.connect(tmp_db)
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "canonical_model" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sqlite_canonical_model.py -v`
Expected: FAIL — `test_canonical_model_defaults_to_none` fails with `TypeError: __init__() got an unexpected keyword argument 'canonical_model'`

- [ ] **Step 3: Add the field to `SessionRecord`**

In `src/models.py`, add one field to the dataclass (after `context`):

```python
    context: str = DEFAULT_CONTEXT  # usage context label, e.g. "work" or "personal"
    canonical_model: str | None = None  # normalized model name, computed by ModelNormalizeMiddleware
```

- [ ] **Step 4: Update the schema, migration, upsert, and unsynced_for in `src/stores/sqlite.py`**

Replace `_CREATE_SESSIONS`:

```python
_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id             TEXT NOT NULL,
    source                 TEXT NOT NULL,
    model                  TEXT NOT NULL,
    canonical_model        TEXT,
    date                   TEXT NOT NULL,
    start_ts               TEXT,
    end_ts                 TEXT,
    project                TEXT,
    turns                  INTEGER DEFAULT 0,
    tool_calls             INTEGER DEFAULT 0,
    input_tokens           INTEGER DEFAULT 0,
    output_tokens           INTEGER DEFAULT 0,
    cache_creation_tokens  INTEGER DEFAULT 0,
    cache_read_tokens      INTEGER DEFAULT 0,
    context_peak_tokens    INTEGER DEFAULT 0,
    reasoning_tokens       INTEGER DEFAULT 0,
    context                TEXT DEFAULT 'personal',
    PRIMARY KEY (session_id, source, model)
)
"""
```

Replace `_UPSERT`:

```python
_UPSERT = """
INSERT OR REPLACE INTO sessions
    (session_id, source, model, canonical_model, date, start_ts, end_ts, project,
     turns, tool_calls, input_tokens, output_tokens,
     cache_creation_tokens, cache_read_tokens,
     context_peak_tokens, reasoning_tokens, context)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
```

In `_migrate()`, after the existing `context` column check, add:

```python
            if "canonical_model" not in cols:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN canonical_model TEXT"
                )
```

Replace `upsert()`:

```python
    def upsert(self, records: list[SessionRecord]) -> int:
        """Persist records using INSERT OR REPLACE (last-write-wins).

        Args:
            records: List of SessionRecord objects to upsert.

        Returns:
            Count of records upserted.
        """
        if not records:
            return 0
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                _UPSERT,
                [
                    (
                        r.session_id, r.source, r.model, r.canonical_model, r.date,
                        r.start_ts, r.end_ts, r.project,
                        r.turns, r.tool_calls, r.input_tokens, r.output_tokens,
                        r.cache_creation_tokens, r.cache_read_tokens,
                        r.context_peak_tokens, r.reasoning_tokens, r.context,
                    )
                    for r in records
                ],
            )
        return len(records)
```

Replace `unsynced_for()`:

```python
    def unsynced_for(self, store_name: str) -> list[SessionRecord]:
        """Return all records not yet synced to the given store."""
        _UNSYNCED = """
        SELECT s.session_id, s.source, s.model, s.canonical_model, s.date,
               s.start_ts, s.end_ts, s.project, s.turns, s.tool_calls,
               s.input_tokens, s.output_tokens,
               s.cache_creation_tokens, s.cache_read_tokens,
               s.context_peak_tokens, s.reasoning_tokens, s.context
        FROM sessions s
        LEFT JOIN sync_log l
            ON s.session_id = l.session_id
            AND s.source = l.source
            AND s.model = l.model
            AND l.store_name = ?
        WHERE l.session_id IS NULL
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(_UNSYNCED, (store_name,)).fetchall()
        return [
            SessionRecord(
                session_id=row[0], source=row[1], model=row[2], canonical_model=row[3],
                date=row[4], start_ts=row[5], end_ts=row[6], project=row[7],
                turns=row[8], tool_calls=row[9], input_tokens=row[10],
                output_tokens=row[11], cache_creation_tokens=row[12],
                cache_read_tokens=row[13], context_peak_tokens=row[14],
                reasoning_tokens=row[15],
                context=row[16] if row[16] is not None else "personal",
            )
            for row in rows
        ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_sqlite_canonical_model.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Run the full existing suite to check for regressions**

Run: `pytest -q`
Expected: All tests pass (existing tests never set `canonical_model`, so it defaults to `None` on both sides of any equality check).

- [ ] **Step 7: Commit**

```bash
git add src/models.py src/stores/sqlite.py tests/test_sqlite_canonical_model.py
git commit -m "feat: add canonical_model field and persist it in SqliteStore"
```

---

### Task 3: Sync-log invalidation on change

**Files:**
- Modify: `src/stores/sqlite.py`
- Test: `tests/test_sqlite_resync.py`

**Interfaces:**
- Consumes: `SqliteStore.upsert()`, `unsynced_for()`, `mark_synced()` from Task 2 (unchanged signatures).
- Produces: `upsert()` now clears matching `sync_log` rows when any persisted field differs from what's already stored — no new public interface, but this is the behavior the backfill workflow (spec's "Backfill workflow" section) depends on.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sqlite_resync.py`:

```python
from __future__ import annotations

from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


def _rec(session_id: str, **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-haiku-4-5-20251001", date="2026-07-01", turns=1)
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def test_upsert_with_changed_field_clears_sync_log(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", turns=1)])
    store.mark_synced([_rec("s1", turns=1)], "supabase")
    assert store.unsynced_for("supabase") == []

    # Re-collect with a changed field (simulates a backfill run)
    store.upsert([_rec("s1", turns=2)])

    assert len(store.unsynced_for("supabase")) == 1


def test_upsert_with_changed_canonical_model_clears_sync_log(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", canonical_model=None)])
    store.mark_synced([_rec("s1", canonical_model=None)], "supabase")
    assert store.unsynced_for("supabase") == []

    store.upsert([_rec("s1", canonical_model="claude-haiku-4-5")])

    unsynced = store.unsynced_for("supabase")
    assert len(unsynced) == 1
    assert unsynced[0].canonical_model == "claude-haiku-4-5"


def test_upsert_with_unchanged_values_does_not_clear_sync_log(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1", turns=1)])
    store.mark_synced([_rec("s1", turns=1)], "supabase")
    assert store.unsynced_for("supabase") == []

    # Re-collect with identical values — nothing actually changed
    store.upsert([_rec("s1", turns=1)])

    assert store.unsynced_for("supabase") == []


def test_upsert_first_insert_does_not_touch_sync_log(tmp_db):
    store = SqliteStore(tmp_db)
    store.upsert([_rec("s1")])
    # No prior sync_log entry existed — nothing to clear, no error
    assert len(store.unsynced_for("supabase")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sqlite_resync.py -v`
Expected: FAIL — `test_upsert_with_changed_field_clears_sync_log` and the canonical_model variant fail (`unsynced_for` still returns `[]` because the current `upsert()` never clears `sync_log`).

- [ ] **Step 3: Replace `upsert()` in `src/stores/sqlite.py` with a diff-before-write version**

```python
    def upsert(self, records: list[SessionRecord]) -> int:
        """Persist records using INSERT OR REPLACE (last-write-wins).

        If a record's stored values differ from what's already in the table,
        its sync_log entries are cleared so unsynced_for() picks it up again
        on the next sync — otherwise a backfill would silently never re-push.

        Args:
            records: List of SessionRecord objects to upsert.

        Returns:
            Count of records upserted.
        """
        if not records:
            return 0
        with closing(self._connect()) as conn, conn:
            for r in records:
                new_row = (
                    r.model, r.canonical_model, r.date, r.start_ts, r.end_ts, r.project,
                    r.turns, r.tool_calls, r.input_tokens, r.output_tokens,
                    r.cache_creation_tokens, r.cache_read_tokens,
                    r.context_peak_tokens, r.reasoning_tokens, r.context,
                )
                existing = conn.execute(
                    """
                    SELECT model, canonical_model, date, start_ts, end_ts, project,
                           turns, tool_calls, input_tokens, output_tokens,
                           cache_creation_tokens, cache_read_tokens,
                           context_peak_tokens, reasoning_tokens, context
                    FROM sessions WHERE session_id = ? AND source = ? AND model = ?
                    """,
                    (r.session_id, r.source, r.model),
                ).fetchone()
                if existing is not None and tuple(existing) != new_row:
                    conn.execute(
                        "DELETE FROM sync_log WHERE session_id = ? AND source = ? AND model = ?",
                        (r.session_id, r.source, r.model),
                    )
                conn.execute(_UPSERT, (r.session_id, r.source) + new_row)
        return len(records)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sqlite_resync.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full existing suite to check for regressions**

Run: `pytest -q`
Expected: All tests pass, including `tests/test_sync_command.py` (its `_seed()` helper only ever inserts each session once before checking sync state, so the new diff-check never fires there).

- [ ] **Step 6: Commit**

```bash
git add src/stores/sqlite.py tests/test_sqlite_resync.py
git commit -m "fix: clear sync_log on upsert when a record's values change"
```

---

### Task 4: `RecordMiddleware` protocol and `ModelNormalizeMiddleware`

**Files:**
- Create: `src/middleware/__init__.py`
- Create: `src/middleware/base.py`
- Create: `src/middleware/model_normalize.py`
- Test: `tests/test_middleware_model_normalize.py`

**Interfaces:**
- Consumes: `normalize_model(raw, source)` from Task 1; `SessionRecord` from Task 2.
- Produces: `RecordMiddleware` Protocol (`name: str`, `applies(records) -> bool`, `process(records) -> list[SessionRecord]`); `ModelNormalizeMiddleware` class implementing it. Task 5 (`TrackerPipeline`) and Task 6 (`collect.py`) consume both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_middleware_model_normalize.py`:

```python
from __future__ import annotations

from src.middleware.model_normalize import ModelNormalizeMiddleware
from src.models import SessionRecord


def _rec(model: str, source: str = "claude_cli") -> SessionRecord:
    return SessionRecord(session_id="s1", source=source, model=model, date="2026-07-01")


def test_applies_always_true():
    mw = ModelNormalizeMiddleware()
    assert mw.applies([_rec("claude-sonnet-4-6")]) is True
    assert mw.applies([]) is True


def test_process_sets_canonical_model_from_date_suffix():
    mw = ModelNormalizeMiddleware()
    [result] = mw.process([_rec("claude-haiku-4-5-20251001")])
    assert result.canonical_model == "claude-haiku-4-5"


def test_process_sets_canonical_model_from_lookup():
    mw = ModelNormalizeMiddleware()
    [result] = mw.process([_rec("claude-sonnet-4.5", source="copilot_cli")])
    assert result.canonical_model == "claude-sonnet-4-5"


def test_process_passthrough_for_unrecognized_model():
    mw = ModelNormalizeMiddleware()
    [result] = mw.process([_rec("gpt-4o", source="copilot_cli")])
    assert result.canonical_model == "gpt-4o"


def test_process_preserves_raw_model():
    mw = ModelNormalizeMiddleware()
    [result] = mw.process([_rec("claude-haiku-4-5-20251001")])
    assert result.model == "claude-haiku-4-5-20251001"


def test_process_handles_batch_of_multiple_records():
    mw = ModelNormalizeMiddleware()
    records = [_rec("claude-haiku-4-5-20251001"), _rec("claude-sonnet-4-6")]
    results = mw.process(records)
    assert [r.canonical_model for r in results] == ["claude-haiku-4-5", "claude-sonnet-4-6"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_middleware_model_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.middleware'`

- [ ] **Step 3: Write the protocol**

Create `src/middleware/base.py`:

```python
"""Pipes-and-Filters middleware protocol for TrackerPipeline."""
from __future__ import annotations

from typing import Protocol

from ..models import SessionRecord


class RecordMiddleware(Protocol):
    """A pluggable batch transform stage between collection and persistence.

    Every applicable middleware transforms the batch and always forwards it
    to the next stage (Pipes-and-Filters) — nothing here short-circuits
    the chain the way Chain of Responsibility would.
    """

    name: str

    def applies(self, records: list[SessionRecord]) -> bool:
        """Return True if this middleware should run for this batch."""
        ...

    def process(self, records: list[SessionRecord]) -> list[SessionRecord]:
        """Transform the batch and return the new batch."""
        ...
```

- [ ] **Step 4: Write the concrete middleware**

Create `src/middleware/model_normalize.py`:

```python
"""Middleware that fills in SessionRecord.canonical_model."""
from __future__ import annotations

from dataclasses import replace

from ..model_normalize import normalize_model
from ..models import SessionRecord


class ModelNormalizeMiddleware:
    """Populates canonical_model via normalize_model() for every record."""

    name = "model_normalize"

    def applies(self, records: list[SessionRecord]) -> bool:
        return True

    def process(self, records: list[SessionRecord]) -> list[SessionRecord]:
        return [
            replace(r, canonical_model=normalize_model(r.model, r.source))
            for r in records
        ]
```

- [ ] **Step 5: Write the package `__init__.py`**

Create `src/middleware/__init__.py`:

```python
"""Pluggable record-transform stages for TrackerPipeline."""

from .base import RecordMiddleware
from .model_normalize import ModelNormalizeMiddleware

__all__ = [
    "RecordMiddleware",
    "ModelNormalizeMiddleware",
]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_middleware_model_normalize.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: Commit**

```bash
git add src/middleware/ tests/test_middleware_model_normalize.py
git commit -m "feat: add RecordMiddleware protocol and ModelNormalizeMiddleware"
```

---

### Task 5: Wire middleware into `TrackerPipeline`

**Files:**
- Modify: `src/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `RecordMiddleware` from Task 4.
- Produces: `TrackerPipeline.middlewares(*mw: RecordMiddleware) -> TrackerPipeline` fluent builder. Task 6 (`collect.py`) consumes this.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py` (add `from dataclasses import replace` to the imports at the top):

```python
from dataclasses import replace


class _StubMiddleware:
    def __init__(self, name, fn):
        self.name = name
        self._fn = fn

    def applies(self, records):
        return True

    def process(self, records):
        return [self._fn(r) for r in records]


class _BoomMiddleware:
    name = "boom"

    def applies(self, records):
        return True

    def process(self, records):
        raise RuntimeError("middleware exploded")


def test_pipeline_runs_middleware_before_store(tmp_db):
    rec = SessionRecord(session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
                        date="2026-06-15", turns=1, input_tokens=10)
    collector = _StubCollector("claude_cli", [rec])
    mw = _StubMiddleware("tag", lambda r: replace(r, canonical_model="TAGGED"))
    (
        TrackerPipeline().add(collector).since(date(2026, 1, 1))
        .middlewares(mw).stores(SqliteStore(tmp_db)).run()
    )
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT canonical_model FROM sessions WHERE session_id='s1'").fetchone()
    assert row[0] == "TAGGED"


def test_pipeline_skips_middleware_when_applies_false(tmp_db):
    rec = SessionRecord(session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
                        date="2026-06-15", turns=1, input_tokens=10)
    collector = _StubCollector("claude_cli", [rec])
    mw = _StubMiddleware("tag", lambda r: replace(r, canonical_model="TAGGED"))
    mw.applies = lambda records: False
    (
        TrackerPipeline().add(collector).since(date(2026, 1, 1))
        .middlewares(mw).stores(SqliteStore(tmp_db)).run()
    )
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT canonical_model FROM sessions WHERE session_id='s1'").fetchone()
    assert row[0] is None


def test_pipeline_middleware_failure_aborts_run(tmp_db):
    rec = SessionRecord(session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
                        date="2026-06-15", turns=1)
    collector = _StubCollector("claude_cli", [rec])
    with pytest.raises(RuntimeError, match="middleware exploded"):
        (
            TrackerPipeline().add(collector).since(date(2026, 1, 1))
            .middlewares(_BoomMiddleware()).stores(SqliteStore(tmp_db)).run()
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — `AttributeError: 'TrackerPipeline' object has no attribute 'middlewares'`

- [ ] **Step 3: Add the middleware plumbing to `src/pipeline.py`**

Add import at the top (alongside the existing imports):

```python
from .middleware.base import RecordMiddleware
```

In `TrackerPipeline.__init__`, add the middleware list:

```python
    def __init__(self) -> None:
        self._collectors: list[ActivityCollector] = []
        self._since: date | None = None
        self._stores: list[SessionStore] = []
        self._context: str = DEFAULT_CONTEXT
        self._middlewares: list[RecordMiddleware] = []
```

Add the fluent builder method (after `add()`):

```python
    def middlewares(self, *mw: RecordMiddleware) -> "TrackerPipeline":
        self._middlewares = list(mw)
        return self
```

In `run()`, insert the middleware loop between the context-stamping line and the sqlite upsert:

```python
        merged = merge_records(records)
        merged = [replace(rec, context=self._context) for rec in merged]

        for mw in self._middlewares:
            if mw.applies(merged):
                merged = mw.process(merged)

        # SQLite (first store) must succeed — exceptions propagate
        written = self._stores[0].upsert(merged)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (all tests in the file, including the 3 new ones)

- [ ] **Step 5: Run the full existing suite to check for regressions**

Run: `pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/pipeline.py tests/test_pipeline.py
git commit -m "feat: wire pluggable RecordMiddleware chain into TrackerPipeline"
```

---

### Task 6: Wire `ModelNormalizeMiddleware` into the `collect` command

**Files:**
- Modify: `src/commands/collect.py`
- Test: `tests/test_tracker_cli.py`

**Interfaces:**
- Consumes: `TrackerPipeline.middlewares()` from Task 5; `ModelNormalizeMiddleware` from Task 4.
- Produces: nothing new — this is the final wiring step that makes normalization active for real `collect` runs.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tracker_cli.py` (add the import alongside the existing ones at the top):

```python
from src.middleware.model_normalize import ModelNormalizeMiddleware
```

```python
def test_build_pipeline_wires_model_normalize_middleware(tmp_path):
    pipeline, store = collect_cmd._build_pipeline(_cfg(tmp_path, "no"))
    if store is not None:
        store.close()
    assert any(isinstance(mw, ModelNormalizeMiddleware) for mw in pipeline._middlewares)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tracker_cli.py -k model_normalize -v`
Expected: FAIL — `assert any(...)` is `False` (empty `_middlewares` list)

- [ ] **Step 3: Wire the middleware in `src/commands/collect.py`**

Add the import (alongside the existing `src.collectors` import):

```python
from src.middleware.model_normalize import ModelNormalizeMiddleware
```

Update `_build_pipeline`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tracker_cli.py -k model_normalize -v`
Expected: PASS

- [ ] **Step 5: Run the full existing suite to check for regressions**

Run: `pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/commands/collect.py tests/test_tracker_cli.py
git commit -m "feat: wire ModelNormalizeMiddleware into the collect pipeline"
```

---

### Task 7: `report.py` groups and filters by canonical model

**Files:**
- Modify: `src/report.py`
- Modify: `src/commands/report.py`
- Test: `tests/test_store_report.py`

**Interfaces:**
- Consumes: `sessions.canonical_model` column from Task 2.
- Produces: nothing new — this is the final consumer of `canonical_model` for display/grouping/filtering.

**Important implementation note:** `GROUP BY` and the `--model` filter must use the full expression `COALESCE(canonical_model, model)`, not an alias named `model` — SQLite resolves a bare `GROUP BY model` to the real `model` *column* when a same-named alias exists in the same query, silently reintroducing the exact fragmentation bug this feature fixes (verified empirically: aliasing to `model` and grouping by that alias grouped `claude-haiku-4-5-20251001` and `claude-haiku-4-5` into separate rows; repeating the full `COALESCE(...)` expression in `GROUP BY` groups them correctly while still aliasing the SELECT output to `model` for backward-compatible dict/JSON keys).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store_report.py`:

```python
def test_period_summary_merges_dated_and_alias_model_variants(tmp_db):
    store = UsageStore(tmp_db)
    store.upsert([
        SessionRecord(
            session_id="s1", source="claude_cli", model="claude-haiku-4-5-20251001",
            canonical_model="claude-haiku-4-5", date="2026-07-01", turns=3,
            input_tokens=100, output_tokens=10,
        ),
        SessionRecord(
            session_id="s2", source="claude_cli", model="claude-haiku-4-5",
            canonical_model="claude-haiku-4-5", date="2026-07-01", turns=2,
            input_tokens=50, output_tokens=5,
        ),
    ])
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="month", summary=True, as_json=True)
    import json as _json
    rows = _json.loads(output)["rows"]
    assert len(rows) == 1
    assert rows[0]["model"] == "claude-haiku-4-5"
    assert rows[0]["turns"] == 5


def test_model_filter_matches_canonical_model(tmp_db):
    store = UsageStore(tmp_db)
    store.upsert([
        SessionRecord(
            session_id="s1", source="claude_cli", model="claude-haiku-4-5-20251001",
            canonical_model="claude-haiku-4-5", date="2026-07-01", turns=1,
        ),
        SessionRecord(
            session_id="s2", source="claude_cli", model="claude-sonnet-4-6",
            canonical_model="claude-sonnet-4-6", date="2026-07-01", turns=1,
        ),
    ])
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day", models=["claude-haiku-4-5"], as_json=True)
    import json as _json
    rows = _json.loads(output)["rows"]
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"


def test_model_filter_falls_back_to_raw_model_when_canonical_unset(tmp_db):
    # Rows written before this feature shipped have canonical_model = NULL;
    # filtering must still work against the raw model in that case.
    store = UsageStore(tmp_db)
    store.upsert([_rec("s1", model="model-a"), _rec("s2", model="model-b")])
    reporter = UsageReporter(tmp_db)
    output = reporter.report(period="day", models=["model-a"], as_json=True)
    import json as _json
    rows = _json.loads(output)["rows"]
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"


def test_detailed_view_shows_raw_and_canonical_model(tmp_db):
    store = UsageStore(tmp_db)
    store.upsert([
        SessionRecord(
            session_id="s1", source="claude_cli", model="claude-haiku-4-5-20251001",
            canonical_model="claude-haiku-4-5", date="2026-07-01", turns=1,
        ),
    ])
    out = UsageReporter(tmp_db).report(detailed=True)
    assert "claude-haiku-4-5-20251001" in out
    assert "claude-haiku-4-5" in out
    assert "Canonical" in out
```

Also update the existing `test_detailed_flag_dumps_all_rows_all_columns` header list (it will otherwise fail once `FullDumpView` adds a column) — replace:

```python
    for header in ("Session", "Source", "Model", "Date", "Start", "End",
                   "Project", "Turns", "Tools", "Input", "Output",
                   "CacheCreate", "CacheRead", "CtxPeak", "Reasoning",
                   "Context", "Synced"):
```

with:

```python
    for header in ("Session", "Source", "Model", "Canonical", "Date", "Start", "End",
                   "Project", "Turns", "Tools", "Input", "Output",
                   "CacheCreate", "CacheRead", "CtxPeak", "Reasoning",
                   "Context", "Synced"):
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store_report.py -v`
Expected: FAIL — the new tests fail (dated/alias rows show as 2 groups, not 1; `models=["claude-haiku-4-5"]` matches nothing since raw `model` differs), and `test_detailed_flag_dumps_all_rows_all_columns` fails on the added `"Canonical"` header not yet present.

- [ ] **Step 3: Update `_make_context` in `src/report.py`**

Replace:

```python
        model_filter = ""
        params: list = []
        if models:
            placeholders = ",".join("?" * len(models))
            model_filter = f" AND model IN ({placeholders})"
            params.extend(models)
```

with:

```python
        model_filter = ""
        params: list = []
        if models:
            placeholders = ",".join("?" * len(models))
            model_filter = f" AND COALESCE(canonical_model, model) IN ({placeholders})"
            params.extend(models)
```

- [ ] **Step 4: Update `SessionsDetailedView`**

Replace the `model,` line in its `SELECT` with:

```python
                COALESCE(canonical_model, model)                       AS model,
```

(No other changes needed in this view — `r["model"]` and the `"Model"` header already work off the new aliased value.)

- [ ] **Step 5: Update `PeriodSummaryView`**

Replace:

```python
        rows = ctx.conn.execute(f"""
            SELECT
                {period_expr}                       AS period,
                source,
                model,
                SUM(turns)                          AS turns,
                SUM(input_tokens)                   AS input_tokens,
                SUM(output_tokens)                  AS output_tokens,
                SUM(cache_creation_tokens)          AS cache_creation_tokens,
                SUM(cache_read_tokens)              AS cache_read_tokens
            FROM sessions
            WHERE {ctx.date_filter}{ctx.model_filter}
            GROUP BY {period_expr}, source, model
            ORDER BY period DESC, input_tokens DESC
        """, ctx.params).fetchall()
```

with:

```python
        rows = ctx.conn.execute(f"""
            SELECT
                {period_expr}                            AS period,
                source,
                COALESCE(canonical_model, model)          AS model,
                SUM(turns)                                AS turns,
                SUM(input_tokens)                         AS input_tokens,
                SUM(output_tokens)                        AS output_tokens,
                SUM(cache_creation_tokens)                AS cache_creation_tokens,
                SUM(cache_read_tokens)                    AS cache_read_tokens
            FROM sessions
            WHERE {ctx.date_filter}{ctx.model_filter}
            GROUP BY {period_expr}, source, COALESCE(canonical_model, model)
            ORDER BY period DESC, input_tokens DESC
        """, ctx.params).fetchall()
```

- [ ] **Step 6: Update `ByProjectView`**

Replace:

```python
        rows = ctx.conn.execute(f"""
            SELECT
                project,
                date,
                model,
                SUM(turns)              AS turns,
                SUM(input_tokens)       AS input_tokens,
                SUM(output_tokens)      AS output_tokens,
                SUM(cache_read_tokens)  AS cache_read_tokens
            FROM sessions
            WHERE project IS NOT NULL
              AND {ctx.date_filter}{ctx.model_filter}
            GROUP BY project, date, model
            ORDER BY SUM(input_tokens + cache_read_tokens) DESC
        """, ctx.params).fetchall()
```

with:

```python
        rows = ctx.conn.execute(f"""
            SELECT
                project,
                date,
                COALESCE(canonical_model, model)  AS model,
                SUM(turns)              AS turns,
                SUM(input_tokens)       AS input_tokens,
                SUM(output_tokens)      AS output_tokens,
                SUM(cache_read_tokens)  AS cache_read_tokens
            FROM sessions
            WHERE project IS NOT NULL
              AND {ctx.date_filter}{ctx.model_filter}
            GROUP BY project, date, COALESCE(canonical_model, model)
            ORDER BY SUM(input_tokens + cache_read_tokens) DESC
        """, ctx.params).fetchall()
```

- [ ] **Step 7: Update `SessionsListView`**

Replace the `model,` line in its `SELECT` with:

```python
                COALESCE(canonical_model, model)                             AS model,
```

- [ ] **Step 8: Update `FullDumpView`**

Replace:

```python
        rows = ctx.conn.execute(f"""
            SELECT
                session_id,
                source,
                model,
                date,
                start_ts,
                end_ts,
                COALESCE(project, '—')  AS project,
                turns,
                tool_calls,
                input_tokens,
                output_tokens,
                cache_creation_tokens,
                cache_read_tokens,
                context_peak_tokens,
                reasoning_tokens,
                context,
                COALESCE((
                    SELECT GROUP_CONCAT(store_name, ',')
                    FROM (
                        SELECT l.store_name
                        FROM sync_log l
                        WHERE l.session_id = sessions.session_id
                          AND l.source = sessions.source
                          AND l.model = sessions.model
                        ORDER BY l.store_name
                    )
                ), '')                  AS synced
            FROM sessions
            WHERE 1=1{ctx.model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, ctx.params).fetchall()
```

with:

```python
        rows = ctx.conn.execute(f"""
            SELECT
                session_id,
                source,
                model,
                COALESCE(canonical_model, '—')  AS canonical_model,
                date,
                start_ts,
                end_ts,
                COALESCE(project, '—')  AS project,
                turns,
                tool_calls,
                input_tokens,
                output_tokens,
                cache_creation_tokens,
                cache_read_tokens,
                context_peak_tokens,
                reasoning_tokens,
                context,
                COALESCE((
                    SELECT GROUP_CONCAT(store_name, ',')
                    FROM (
                        SELECT l.store_name
                        FROM sync_log l
                        WHERE l.session_id = sessions.session_id
                          AND l.source = sessions.source
                          AND l.model = sessions.model
                        ORDER BY l.store_name
                    )
                ), '')                  AS synced
            FROM sessions
            WHERE 1=1{ctx.model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, ctx.params).fetchall()
```

And replace its `_format_table` call:

```python
        return _format_table(
            ctx.hit_rate,
            ctx.cost_saved,
            headers=["Session", "Source", "Model", "Date", "Start", "End",
                     "Project", "Turns", "Tools", "Input", "Output",
                     "CacheCreate", "CacheRead", "CtxPeak", "Reasoning",
                     "Context", "Synced"],
            rows=[
                [r["session_id"], r["source"], r["model"], r["date"],
                 (r["start_ts"] or "")[:19], (r["end_ts"] or "")[:19],
                 r["project"], r["turns"], r["tool_calls"],
                 r["input_tokens"], r["output_tokens"],
                 r["cache_creation_tokens"], r["cache_read_tokens"],
                 r["context_peak_tokens"], r["reasoning_tokens"],
                 r["context"], r["synced"]]
                for r in rows
            ],
        )
```

with:

```python
        return _format_table(
            ctx.hit_rate,
            ctx.cost_saved,
            headers=["Session", "Source", "Model", "Canonical", "Date", "Start", "End",
                     "Project", "Turns", "Tools", "Input", "Output",
                     "CacheCreate", "CacheRead", "CtxPeak", "Reasoning",
                     "Context", "Synced"],
            rows=[
                [r["session_id"], r["source"], r["model"], r["canonical_model"], r["date"],
                 (r["start_ts"] or "")[:19], (r["end_ts"] or "")[:19],
                 r["project"], r["turns"], r["tool_calls"],
                 r["input_tokens"], r["output_tokens"],
                 r["cache_creation_tokens"], r["cache_read_tokens"],
                 r["context_peak_tokens"], r["reasoning_tokens"],
                 r["context"], r["synced"]]
                for r in rows
            ],
        )
```

- [ ] **Step 9: Update the `--model` flag help text in `src/commands/report.py`**

Replace:

```python
        parser.add_argument("--model", action="append", dest="model",
                            help="filter to model(s) (repeatable)")
```

with:

```python
        parser.add_argument("--model", action="append", dest="model",
                            help="filter to canonical model name(s) (repeatable)")
```

- [ ] **Step 10: Run test to verify it passes**

Run: `pytest tests/test_store_report.py -v`
Expected: PASS (all tests, including the 4 new ones and the updated header assertion)

- [ ] **Step 11: Run the full existing suite to check for regressions**

Run: `pytest -q`
Expected: All tests pass.

- [ ] **Step 12: Commit**

```bash
git add src/report.py src/commands/report.py tests/test_store_report.py
git commit -m "feat: group and filter reports by canonical_model with raw-model fallback"
```

---

## Manual verification (after all tasks)

Once all 7 tasks are committed, do one end-to-end sanity check:

```bash
python3 tracker.py collect --lookback 90
python3 tracker.py report --summary --period month
```

Confirm the `Model` column shows normalized names (no trailing `-YYYYMMDD` suffixes), and that a model reported under both a dated snapshot and its alias in your local logs now appears as a single merged row.
