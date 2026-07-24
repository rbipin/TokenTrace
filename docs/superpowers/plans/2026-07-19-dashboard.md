# Local Usage Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `tokentracer dashboard` command that serves a read-only JSON API plus a built React frontend, visualizing `usage.db` (totals, trends, per-project/per-model/per-source breakdowns) without any new data collection.

**Architecture:** A new `src/dashboard/` package holds pure SQL query functions (`queries.py`) and an `http.server`-based handler (`server.py`) that serves `/api/*` JSON routes plus the built static frontend from `frontend/dist/`. A new `src/dashboard/daemon.py` handles macOS (`launchctl`/plist) and Windows (`schtasks`) persistent-service install/remove for `--daemon`/`--stop`. `src/commands/dashboard.py` wires it all into the existing `Command` registry. The frontend is a separate Vite + React app in `frontend/`, built ahead of time (`npm install && npm run build`); the Python package stays stdlib-only at runtime.

**Tech Stack:** Python stdlib (`http.server`, `sqlite3`, `subprocess`, `platform`) for the backend; React + Vite (JavaScript, no TypeScript) for the frontend, no charting library — heatmap/trend/bars are hand-rolled SVG/CSS.

## Global Constraints

- Backend runtime stays stdlib-only — no new pip dependency (per `CLAUDE.md`).
- API binds to `127.0.0.1` only, no auth.
- Fixed default port `8420`, overridable with `--port` (must be 1–65535).
- Frontend refresh is manual only — no polling/auto-refresh interval anywhere.
- `frontend/node_modules/` and `frontend/dist/` are gitignored, never committed.
- Context breakdown covers exactly: Input, Output, Cache Read, Cache Creation, Reasoning tokens — no System prompt/Custom agents/MCP servers/Skills categories (not derivable without new collector work — out of scope, see spec).
- Harness cards are data-driven from whatever `source` values exist in `sessions` — never a hardcoded list of harnesses.
- Existing `src/report.py` / `UsageReporter` is not modified — the dashboard's query shapes don't match `report()`'s existing strategies, so `src/dashboard/queries.py` is a new, independent read layer directly against `sessions`/`sync_log` (avoids duplicating `report.py`'s dispatch machinery for shapes it was never designed to produce).
- `period=week` and `period=custom` are dashboard-only additions, defined locally in `src/dashboard/queries.py` — the existing `tracker.py report` CLI's period enum (`day`/`month`/`year`/`all`) is untouched.

---

### Task 1: Dashboard query package + `summary()`

**Files:**
- Create: `src/dashboard/__init__.py`
- Create: `src/dashboard/queries.py`
- Test: `tests/test_dashboard_queries.py`

**Interfaces:**
- Produces: `src.dashboard.queries.date_filter(period: str, start: str | None, end: str | None) -> tuple[str, list]` — returns a SQL `WHERE` fragment (no leading `AND`) and its bind params. Raises `ValueError` for `period == "custom"` with missing `start`/`end`, and for any unrecognized `period`.
- Produces: `src.dashboard.queries.summary(conn: sqlite3.Connection, period: str, start: str | None = None, end: str | None = None, project: str | None = None, source: str | None = None) -> dict` with keys `total_tokens`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `reasoning_tokens`, `session_count`, `active_days`, `first_date`, `harnesses` (list of `{source, tokens, model_count, pct}`), `models` (list of `{model, tokens, pct}`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_queries.py
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from src.dashboard import queries
from src.stores.sqlite import SqliteStore
from src.models import SessionRecord


def _rec(session_id: str, date_str: str, **kwargs) -> SessionRecord:
    defaults = dict(source="claude_cli", model="claude-sonnet-4-6", date=date_str)
    defaults.update(kwargs)
    return SessionRecord(session_id=session_id, **defaults)


def _conn(tmp_db) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    return conn


def test_date_filter_known_periods():
    for period in ("all", "day", "week", "month", "year"):
        where, params = queries.date_filter(period, None, None)
        assert isinstance(where, str) and where
        assert params == []


def test_date_filter_custom_requires_bounds():
    with pytest.raises(ValueError):
        queries.date_filter("custom", None, None)
    where, params = queries.date_filter("custom", "2026-01-01", "2026-01-31")
    assert params == ["2026-01-01", "2026-01-31"]


def test_date_filter_unknown_period():
    with pytest.raises(ValueError):
        queries.date_filter("bogus", None, None)


def test_summary_totals_and_harnesses(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=100, output_tokens=50, source="claude_cli"),
        _rec("s2", today, input_tokens=200, output_tokens=0, source="copilot_cli",
             model="gpt-5"),
    ])
    result = queries.summary(_conn(tmp_db), "all")
    assert result["total_tokens"] == 350
    assert result["session_count"] == 2
    sources = {h["source"] for h in result["harnesses"]}
    assert sources == {"claude_cli", "copilot_cli"}
    claude = next(h for h in result["harnesses"] if h["source"] == "claude_cli")
    assert claude["tokens"] == 150
    assert round(claude["pct"], 4) == round(150 / 350, 4)


def test_summary_empty_db_returns_zeros(tmp_db):
    SqliteStore(tmp_db)  # creates schema, no rows
    result = queries.summary(_conn(tmp_db), "all")
    assert result["total_tokens"] == 0
    assert result["harnesses"] == []
    assert result["models"] == []


def test_summary_project_filter(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=100, project="proj-a"),
        _rec("s2", today, input_tokens=999, project="proj-b"),
    ])
    result = queries.summary(_conn(tmp_db), "all", project="proj-a")
    assert result["total_tokens"] == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_queries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.dashboard'`

- [ ] **Step 3: Implement `src/dashboard/__init__.py` and `date_filter`/`summary`**

```python
# src/dashboard/__init__.py
"""Read-only query layer and HTTP server backing `tokentracer dashboard`."""
```

```python
# src/dashboard/queries.py
from __future__ import annotations

import sqlite3

_TOKENS_EXPR = (
    "input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens"
)

_DATE_RANGE_SQL = {
    "all":   "1=1",
    "day":   "date = date('now', 'localtime')",
    "week":  "date >= date('now', '-6 days', 'localtime')",
    "month": "date >= date('now', 'start of month', 'localtime')",
    "year":  "date >= date('now', 'start of year', 'localtime')",
}


def date_filter(period: str, start: str | None, end: str | None) -> tuple[str, list]:
    """Return a WHERE fragment (no leading AND) and its bind params."""
    if period == "custom":
        if not start or not end:
            raise ValueError("period=custom requires both start and end")
        return "date BETWEEN ? AND ?", [start, end]
    if period not in _DATE_RANGE_SQL:
        raise ValueError(f"period must be one of {list(_DATE_RANGE_SQL) + ['custom']}")
    return _DATE_RANGE_SQL[period], []


def summary(
    conn: sqlite3.Connection,
    period: str,
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    source: str | None = None,
) -> dict:
    where, params = date_filter(period, start, end)
    extra = ""
    if project:
        extra += " AND project = ?"
        params.append(project)
    if source:
        extra += " AND source = ?"
        params.append(source)

    totals = conn.execute(f"""
        SELECT
            COALESCE(SUM(input_tokens), 0)           AS input_tokens,
            COALESCE(SUM(output_tokens), 0)          AS output_tokens,
            COALESCE(SUM(cache_read_tokens), 0)      AS cache_read_tokens,
            COALESCE(SUM(cache_creation_tokens), 0)  AS cache_creation_tokens,
            COALESCE(SUM(reasoning_tokens), 0)       AS reasoning_tokens,
            COUNT(*)                                 AS session_count,
            COUNT(DISTINCT date)                     AS active_days,
            MIN(date)                                AS first_date
        FROM sessions
        WHERE {where}{extra}
    """, params).fetchone()

    total_tokens = (
        totals["input_tokens"] + totals["output_tokens"]
        + totals["cache_read_tokens"] + totals["cache_creation_tokens"]
    )

    harness_rows = conn.execute(f"""
        SELECT source,
               SUM({_TOKENS_EXPR}) AS tokens,
               COUNT(DISTINCT COALESCE(canonical_model, model)) AS model_count
        FROM sessions
        WHERE {where}{extra}
        GROUP BY source
        ORDER BY tokens DESC
    """, params).fetchall()

    model_rows = conn.execute(f"""
        SELECT COALESCE(canonical_model, model) AS model,
               SUM({_TOKENS_EXPR}) AS tokens
        FROM sessions
        WHERE {where}{extra}
        GROUP BY COALESCE(canonical_model, model)
        ORDER BY tokens DESC
    """, params).fetchall()

    def pct(tokens: int) -> float:
        return (tokens / total_tokens) if total_tokens else 0.0

    return {
        "total_tokens": total_tokens,
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "cache_read_tokens": totals["cache_read_tokens"],
        "cache_creation_tokens": totals["cache_creation_tokens"],
        "reasoning_tokens": totals["reasoning_tokens"],
        "session_count": totals["session_count"],
        "active_days": totals["active_days"],
        "first_date": totals["first_date"],
        "harnesses": [
            {"source": r["source"], "tokens": r["tokens"],
             "model_count": r["model_count"], "pct": pct(r["tokens"])}
            for r in harness_rows
        ],
        "models": [
            {"model": r["model"], "tokens": r["tokens"], "pct": pct(r["tokens"])}
            for r in model_rows
        ],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_queries.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dashboard/__init__.py src/dashboard/queries.py tests/test_dashboard_queries.py
git commit -m "feat: add dashboard query layer with summary()"
```

---

### Task 2: `heatmap()` and `trend()` queries

**Files:**
- Modify: `src/dashboard/queries.py`
- Test: `tests/test_dashboard_queries.py`

**Interfaces:**
- Consumes: nothing new from Task 1 beyond the module itself.
- Produces: `queries.heatmap(conn, days: int = 180) -> list[dict]` — `[{date, tokens}]` ascending by date, only dates with data.
- Produces: `queries.trend(conn, days: int = 30) -> list[dict]` — `[{date, source, tokens}]` ascending by date then source.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_dashboard_queries.py

def test_heatmap_returns_daily_totals(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([_rec("s1", today, input_tokens=100)])
    result = queries.heatmap(_conn(tmp_db), days=180)
    assert result == [{"date": today, "tokens": 100}]


def test_trend_breaks_down_by_source(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=100, source="claude_cli"),
        _rec("s2", today, input_tokens=40, source="copilot_cli", model="gpt-5"),
    ])
    result = queries.trend(_conn(tmp_db), days=30)
    by_source = {r["source"]: r["tokens"] for r in result}
    assert by_source == {"claude_cli": 100, "copilot_cli": 40}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_queries.py -k "heatmap or trend" -v`
Expected: FAIL with `AttributeError: module 'src.dashboard.queries' has no attribute 'heatmap'`

- [ ] **Step 3: Implement**

```python
# append to src/dashboard/queries.py

def heatmap(conn: sqlite3.Connection, days: int = 180) -> list[dict]:
    rows = conn.execute(f"""
        SELECT date, SUM({_TOKENS_EXPR}) AS tokens
        FROM sessions
        WHERE date >= date('now', ?, 'localtime')
        GROUP BY date
        ORDER BY date
    """, [f"-{days - 1} days"]).fetchall()
    return [{"date": r["date"], "tokens": r["tokens"]} for r in rows]


def trend(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    rows = conn.execute(f"""
        SELECT date, source, SUM({_TOKENS_EXPR}) AS tokens
        FROM sessions
        WHERE date >= date('now', ?, 'localtime')
        GROUP BY date, source
        ORDER BY date, source
    """, [f"-{days - 1} days"]).fetchall()
    return [{"date": r["date"], "source": r["source"], "tokens": r["tokens"]} for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_queries.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dashboard/queries.py tests/test_dashboard_queries.py
git commit -m "feat: add dashboard heatmap and trend queries"
```

---

### Task 3: `projects()` and `project_detail()` queries

**Files:**
- Modify: `src/dashboard/queries.py`
- Test: `tests/test_dashboard_queries.py`

**Interfaces:**
- Consumes: `date_filter`, `summary` from Task 1.
- Produces: `queries.projects(conn, period: str, start=None, end=None) -> list[dict]` — `[{project, tokens}]` sorted descending, only rows with a non-null `project`.
- Produces: `queries.project_detail(conn, project: str, period: str, start=None, end=None) -> dict` — same shape as `summary()`, scoped to one project.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_dashboard_queries.py

def test_projects_sorted_descending(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=50, project="small"),
        _rec("s2", today, input_tokens=500, project="big"),
        _rec("s3", today, input_tokens=10),  # no project, excluded
    ])
    result = queries.projects(_conn(tmp_db), "all")
    assert [r["project"] for r in result] == ["big", "small"]


def test_project_detail_scopes_to_one_project(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        _rec("s1", today, input_tokens=100, project="proj-a"),
        _rec("s2", today, input_tokens=999, project="proj-b"),
    ])
    result = queries.project_detail(_conn(tmp_db), "proj-a", "all")
    assert result["total_tokens"] == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_queries.py -k "projects or project_detail" -v`
Expected: FAIL with `AttributeError: module 'src.dashboard.queries' has no attribute 'projects'`

- [ ] **Step 3: Implement**

```python
# append to src/dashboard/queries.py

def projects(
    conn: sqlite3.Connection, period: str, start: str | None = None, end: str | None = None,
) -> list[dict]:
    where, params = date_filter(period, start, end)
    rows = conn.execute(f"""
        SELECT project, SUM({_TOKENS_EXPR}) AS tokens
        FROM sessions
        WHERE project IS NOT NULL AND {where}
        GROUP BY project
        ORDER BY tokens DESC
    """, params).fetchall()
    return [{"project": r["project"], "tokens": r["tokens"]} for r in rows]


def project_detail(
    conn: sqlite3.Connection, project: str, period: str,
    start: str | None = None, end: str | None = None,
) -> dict:
    return summary(conn, period, start=start, end=end, project=project)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_queries.py -v`
Expected: PASS (all 10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dashboard/queries.py tests/test_dashboard_queries.py
git commit -m "feat: add dashboard per-project queries"
```

---

### Task 4: Track last collect run (`run_log` table)

**Files:**
- Modify: `src/stores/sqlite.py`
- Modify: `src/commands/collect.py`
- Test: `tests/test_sqlite_run_log.py`
- Test: `tests/test_tracker_cli.py`

**Interfaces:**
- Produces: `SqliteStore.record_run(timestamp: str | None = None) -> None` — upserts the single row (`id = 1`) in a new `run_log` table, using `datetime('now')` when `timestamp` is omitted. Called unconditionally by `CollectCommand.run()` at the end of every `collect` invocation, including idempotent no-op runs — this is what makes `last_collected_at` (Task 5) distinct from `most_recent_data_at`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sqlite_run_log.py
from __future__ import annotations

import sqlite3

from src.stores.sqlite import SqliteStore


def test_record_run_inserts_single_row(tmp_db):
    store = SqliteStore(tmp_db)
    store.record_run()
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT id, ran_at FROM run_log").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1
    assert rows[0][1] is not None


def test_record_run_upserts_not_grows(tmp_db):
    store = SqliteStore(tmp_db)
    store.record_run("2026-01-01T00:00:00")
    store.record_run("2026-01-02T00:00:00")
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT ran_at FROM run_log").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2026-01-02T00:00:00"


def test_record_run_accepts_explicit_timestamp(tmp_db):
    store = SqliteStore(tmp_db)
    store.record_run("2026-07-19T10:00:00+00:00")
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT ran_at FROM run_log WHERE id = 1").fetchone()
    assert row[0] == "2026-07-19T10:00:00+00:00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sqlite_run_log.py -v`
Expected: FAIL with `AttributeError: 'SqliteStore' object has no attribute 'record_run'`

- [ ] **Step 3: Implement `run_log` table + `record_run`**

```python
# src/stores/sqlite.py — add alongside _CREATE_SESSIONS / _CREATE_SYNC_LOG
_CREATE_RUN_LOG = """
CREATE TABLE IF NOT EXISTS run_log (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    ran_at  TEXT NOT NULL
)
"""
```

```python
# src/stores/sqlite.py — in _migrate(), alongside the other conn.execute(_CREATE_*) calls
            conn.execute(_CREATE_SESSIONS)
            conn.execute(_CREATE_SYNC_LOG)
            conn.execute(_CREATE_RUN_LOG)
```

```python
# src/stores/sqlite.py — new method on SqliteStore
    def record_run(self, timestamp: str | None = None) -> None:
        """Record that `collect` executed, upserting the single run_log row."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO run_log (id, ran_at) VALUES (1, COALESCE(?, datetime('now')))
                ON CONFLICT(id) DO UPDATE SET ran_at = excluded.ran_at
                """,
                (timestamp,),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sqlite_run_log.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stores/sqlite.py tests/test_sqlite_run_log.py
git commit -m "feat: add run_log table and SqliteStore.record_run"
```

- [ ] **Step 6: Write the failing `CollectCommand` wiring test**

```python
# append to tests/test_tracker_cli.py

def test_cmd_collect_records_run(tmp_path, monkeypatch):
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
        def __init__(self):
            self.record_run_calls = 0

        def record_run(self):
            self.record_run_calls += 1

        def close(self):
            pass

    spy = SpyStore()
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, **kw: _cfg(tmp_path, "no")))
    monkeypatch.setattr(collect_cmd, "_build_pipeline", lambda cfg: (FakePipeline(), None))
    monkeypatch.setattr(collect_cmd, "_build_stores", lambda cfg: [spy])

    parser = tracker.build_parser()
    args = parser.parse_args(["collect"])
    assert args.run(args) == 0
    assert spy.record_run_calls == 1
```

- [ ] **Step 7: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tracker_cli.py -k test_cmd_collect_records_run -v`
Expected: FAIL with `assert 0 == 1` (nothing calls `record_run` yet)

- [ ] **Step 8: Wire `record_run` into `CollectCommand.run()`**

```python
# src/commands/collect.py — in CollectCommand.run(), right after the pipeline run's
# try/finally block (before the "if len(stores) > 1" sync-sweep check)
        stores[0].record_run()
```

`stores[0]` is always the local `SqliteStore` per `_build_stores()` — called unconditionally, whether or not the run found new records, so a completed idempotent run still updates `last_collected_at`.

- [ ] **Step 9: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tracker_cli.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 10: Commit**

```bash
git add src/commands/collect.py tests/test_tracker_cli.py
git commit -m "feat: record collect run timestamp on every invocation"
```

---

### Task 5: `sync_status()` and `meta()` queries

**Files:**
- Modify: `src/dashboard/queries.py`
- Test: `tests/test_dashboard_queries.py`

**Interfaces:**
- Consumes: the `run_log` table written by `SqliteStore.record_run` (Task 4).
- Produces: `queries.sync_status(conn) -> dict` — `{"last_collected_at": <ran_at from run_log, or None>, "stores": [{"name", "last_synced_at"}]}`; `last_synced_at` per store is `MAX(synced_at)` grouped by `store_name` in `sync_log`.
- Produces: `queries.meta(conn) -> dict` — `{"most_recent_data_at": <MAX(end_ts) across sessions, or None>}`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_dashboard_queries.py

def test_sync_status_groups_by_store(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([_rec("s1", today, input_tokens=10)])
    store.mark_synced([_rec("s1", today, input_tokens=10)], "supabase")
    result = queries.sync_status(_conn(tmp_db))
    assert len(result["stores"]) == 1
    assert result["stores"][0]["name"] == "supabase"
    assert result["stores"][0]["last_synced_at"] is not None


def test_sync_status_empty(tmp_db):
    SqliteStore(tmp_db)
    result = queries.sync_status(_conn(tmp_db))
    assert result == {"last_collected_at": None, "stores": []}


def test_sync_status_includes_last_collected_at(tmp_db):
    store = SqliteStore(tmp_db)
    store.record_run("2026-07-19T10:00:00+00:00")
    result = queries.sync_status(_conn(tmp_db))
    assert result["last_collected_at"] == "2026-07-19T10:00:00+00:00"


def test_meta_most_recent_data(tmp_db):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([_rec("s1", today, end_ts="2026-07-19T10:00:00+00:00")])
    result = queries.meta(_conn(tmp_db))
    assert result["most_recent_data_at"] == "2026-07-19T10:00:00+00:00"


def test_meta_empty_db(tmp_db):
    SqliteStore(tmp_db)
    result = queries.meta(_conn(tmp_db))
    assert result["most_recent_data_at"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_queries.py -k "sync_status or meta" -v`
Expected: FAIL with `AttributeError: module 'src.dashboard.queries' has no attribute 'sync_status'`

- [ ] **Step 3: Implement**

```python
# append to src/dashboard/queries.py

def sync_status(conn: sqlite3.Connection) -> dict:
    run_row = conn.execute("SELECT ran_at FROM run_log WHERE id = 1").fetchone()
    rows = conn.execute("""
        SELECT store_name, MAX(synced_at) AS last_synced_at
        FROM sync_log
        GROUP BY store_name
        ORDER BY store_name
    """).fetchall()
    return {
        "last_collected_at": run_row["ran_at"] if run_row else None,
        "stores": [
            {"name": r["store_name"], "last_synced_at": r["last_synced_at"]}
            for r in rows
        ],
    }


def meta(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT MAX(end_ts) AS most_recent FROM sessions").fetchone()
    return {"most_recent_data_at": row["most_recent"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_queries.py -v`
Expected: PASS (all 16 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dashboard/queries.py tests/test_dashboard_queries.py
git commit -m "feat: add dashboard sync-status and meta queries"
```

---

### Task 6: HTTP API server

**Files:**
- Create: `src/dashboard/server.py`
- Test: `tests/test_dashboard_server.py`

**Interfaces:**
- Consumes: every function in `src.dashboard.queries` from Tasks 1–3 and 5.
- Produces: `src.dashboard.server.make_server(db_path: Path, static_dir: Path, port: int) -> http.server.ThreadingHTTPServer` — binds to `127.0.0.1:<port>` (port `0` lets the OS assign one for tests; read back via `server.server_address[1]`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_server.py
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from src.dashboard.server import make_server
from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


@pytest.fixture
def running_server(tmp_db, tmp_path):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        SessionRecord(session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
                      date=today, input_tokens=100, output_tokens=20, project="proj-a"),
    ])
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")

    server = make_server(tmp_db, static_dir, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=2)


def _get(port: int, path: str) -> tuple[int, dict | str]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url) as resp:
            body = resp.read().decode("utf-8")
            ct = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(body) if "json" in ct else body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


def test_summary_endpoint(running_server):
    status, body = _get(running_server, "/api/summary?period=all")
    assert status == 200
    assert body["total_tokens"] == 120


def test_summary_endpoint_invalid_period_returns_400(running_server):
    status, body = _get(running_server, "/api/summary?period=bogus")
    assert status == 400
    assert "error" in body


def test_heatmap_endpoint(running_server):
    status, body = _get(running_server, "/api/heatmap?days=30")
    assert status == 200
    assert isinstance(body, list)


def test_project_detail_endpoint(running_server):
    status, body = _get(running_server, "/api/projects/detail?project=proj-a&period=all")
    assert status == 200
    assert body["total_tokens"] == 120


def test_sync_status_endpoint(running_server):
    status, body = _get(running_server, "/api/sync-status")
    assert status == 200
    assert body == {"last_collected_at": None, "stores": []}


def test_meta_endpoint(running_server):
    status, body = _get(running_server, "/api/meta")
    assert status == 200
    assert "most_recent_data_at" in body


def test_unknown_api_route_returns_404(running_server):
    status, body = _get(running_server, "/api/nope")
    assert status == 404


def test_static_index_served(running_server):
    status, body = _get(running_server, "/")
    assert status == 200
    assert "dashboard" in body


def test_static_path_traversal_blocked(running_server):
    status, _ = _get(running_server, "/../../etc/passwd")
    assert status in (403, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.dashboard.server'`

- [ ] **Step 3: Implement**

```python
# src/dashboard/server.py
from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from src.dashboard import queries

_CONTENT_TYPES = {
    ".html": "text/html", ".js": "application/javascript",
    ".css": "text/css", ".json": "application/json",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}


class _DashboardHandler(BaseHTTPRequestHandler):
    db_path: Path
    static_dir: Path

    def log_message(self, format: str, *args) -> None:
        pass  # daemon mode redirects stdout/stderr to dashboard.log; keep it quiet

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed.path, qs)
        else:
            self._handle_static(parsed.path)

    def _handle_api(self, path: str, qs: dict) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            data = self._dispatch(conn, path, qs)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception:
            self._json(500, {"error": "internal error"})
            return
        finally:
            conn.close()
        if data is None:
            self._json(404, {"error": "not found"})
        else:
            self._json(200, data)

    def _dispatch(self, conn: sqlite3.Connection, path: str, qs: dict) -> dict | list | None:
        if path == "/api/summary":
            return queries.summary(
                conn, qs.get("period", "day"), qs.get("start"), qs.get("end"),
                qs.get("project"), qs.get("source"),
            )
        if path == "/api/heatmap":
            return queries.heatmap(conn, int(qs.get("days", 180)))
        if path == "/api/trend":
            return queries.trend(conn, int(qs.get("days", 30)))
        if path == "/api/projects":
            return queries.projects(conn, qs.get("period", "all"), qs.get("start"), qs.get("end"))
        if path == "/api/projects/detail":
            project = qs.get("project")
            if not project:
                raise ValueError("project query param is required")
            return queries.project_detail(
                conn, unquote(project), qs.get("period", "all"), qs.get("start"), qs.get("end"),
            )
        if path == "/api/sync-status":
            return queries.sync_status(conn)
        if path == "/api/meta":
            return queries.meta(conn)
        return None

    def _handle_static(self, path: str) -> None:
        rel = unquote(path.lstrip("/")) or "index.html"
        file_path = (self.static_dir / rel).resolve()
        static_root = self.static_dir.resolve()
        if static_root not in file_path.parents and file_path != static_root:
            self._json(403, {"error": "forbidden"})
            return
        if not file_path.exists() or file_path.is_dir():
            file_path = static_root / "index.html"
        if not file_path.exists():
            self._json(404, {"error": "not found"})
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(file_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(db_path: Path, static_dir: Path, port: int) -> ThreadingHTTPServer:
    handler_cls = type("BoundDashboardHandler", (_DashboardHandler,),
                        {"db_path": db_path, "static_dir": static_dir})
    return ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_server.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dashboard/server.py tests/test_dashboard_server.py
git commit -m "feat: add dashboard HTTP API server"
```

---

### Task 7: Daemon lifecycle (macOS + Windows)

**Files:**
- Create: `src/dashboard/daemon.py`
- Test: `tests/test_dashboard_daemon.py`

**Interfaces:**
- Produces: `src.dashboard.daemon.resolve_executable() -> list[str]` — argv prefix to invoke `tokentracer` (PATH lookup, else `[sys.executable, <repo tracker.py>]`).
- Produces: `src.dashboard.daemon.install(port: int) -> None` — raises `RuntimeError` on unsupported OS or on subprocess failure.
- Produces: `src.dashboard.daemon.uninstall() -> None` — no-op (does not raise) if nothing is registered; raises `RuntimeError` on unsupported OS or on a real subprocess failure.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_daemon.py
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from src.dashboard import daemon


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def _fail(*args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="boom")


def test_resolve_executable_prefers_path(monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "/usr/local/bin/tokentracer")
    assert daemon.resolve_executable() == ["/usr/local/bin/tokentracer"]


def test_resolve_executable_falls_back_to_python(monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda name: None)
    argv = daemon.resolve_executable()
    assert argv[0] == daemon.sys.executable
    assert argv[1].endswith("tracker.py")


def test_install_macos_writes_plist_and_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(daemon.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "/usr/local/bin/tokentracer")
    calls = []
    with patch.object(daemon.subprocess, "run", side_effect=lambda *a, **k: (calls.append(a), _ok())[1]):
        daemon.install(8420)
    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.ai-token-tracer.dashboard.plist"
    assert plist_path.exists()
    content = plist_path.read_text()
    assert "8420" in content
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content
    assert calls[0][0][:2] == ["launchctl", "unload"]
    assert calls[1][0][:2] == ["launchctl", "load"]


def test_install_macos_raises_on_load_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(daemon.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "/usr/local/bin/tokentracer")
    with patch.object(daemon.subprocess, "run", side_effect=[_ok(), _fail()]):
        with pytest.raises(RuntimeError, match="launchctl load failed"):
            daemon.install(8420)


def test_uninstall_macos_noop_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(daemon.Path, "home", lambda: tmp_path)
    daemon.uninstall()  # should not raise


def test_install_windows_creates_task(monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Windows")
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "C:\\tools\\tokentracer.exe")
    with patch.object(daemon.subprocess, "run", return_value=_ok()) as mock_run:
        daemon.install(8420)
    args = mock_run.call_args[0][0]
    assert args[:3] == ["schtasks", "/Create", "/F"]
    assert "ai-token-tracer-dashboard" in args


def test_install_unsupported_os_raises(monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="unsupported OS"):
        daemon.install(8420)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_daemon.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.dashboard.daemon'`

- [ ] **Step 3: Implement**

```python
# src/dashboard/daemon.py
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

_PLIST_LABEL = "com.ai-token-tracer.dashboard"
_TASK_NAME = "ai-token-tracer-dashboard"


def resolve_executable() -> list[str]:
    exe = shutil.which("tokentracer")
    if exe:
        return [exe]
    repo_tracker = Path(__file__).resolve().parents[2] / "tracker.py"
    return [sys.executable, str(repo_tracker)]


def install(port: int) -> None:
    system = platform.system()
    if system == "Darwin":
        _install_macos(port)
    elif system == "Windows":
        _install_windows(port)
    else:
        raise RuntimeError(f"unsupported OS for dashboard daemon: {system}")


def uninstall() -> None:
    system = platform.system()
    if system == "Darwin":
        _uninstall_macos()
    elif system == "Windows":
        _uninstall_windows()
    else:
        raise RuntimeError(f"unsupported OS for dashboard daemon: {system}")


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"


def _install_macos(port: int) -> None:
    argv = resolve_executable() + ["dashboard", "--port", str(port)]
    log_path = Path.home() / ".tokentracer" / "dashboard.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    args_xml = "".join(f"<string>{a}</string>" for a in argv)
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"    <key>Label</key><string>{_PLIST_LABEL}</string>\n"
        f"    <key>ProgramArguments</key><array>{args_xml}</array>\n"
        "    <key>RunAtLoad</key><true/>\n"
        "    <key>KeepAlive</key><true/>\n"
        f"    <key>StandardOutPath</key><string>{log_path}</string>\n"
        f"    <key>StandardErrorPath</key><string>{log_path}</string>\n"
        "</dict>\n</plist>\n"
    )
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    result = subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"launchctl load failed: {result.stderr.strip()}")


def _uninstall_macos() -> None:
    path = _plist_path()
    if not path.exists():
        return
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    path.unlink()


def _install_windows(port: int) -> None:
    argv = resolve_executable() + ["dashboard", "--port", str(port)]
    action = " ".join(f'"{a}"' if " " in a else a for a in argv)
    result = subprocess.run(
        ["schtasks", "/Create", "/F", "/TN", _TASK_NAME,
         "/TR", action, "/SC", "ONLOGON", "/RL", "LIMITED"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"schtasks /Create failed: {result.stderr.strip()}")


def _uninstall_windows() -> None:
    result = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", _TASK_NAME],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and "cannot find" not in result.stderr.lower():
        raise RuntimeError(f"schtasks /Delete failed: {result.stderr.strip()}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_daemon.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dashboard/daemon.py tests/test_dashboard_daemon.py
git commit -m "feat: add dashboard daemon install/uninstall for macOS and Windows"
```

---

### Task 8: `DashboardCommand` + CLI registration

**Files:**
- Create: `src/commands/dashboard.py`
- Modify: `src/commands/__init__.py`
- Test: `tests/test_dashboard_command.py`

**Interfaces:**
- Consumes: `src.dashboard.daemon.install`, `daemon.uninstall` (Task 7); `src.dashboard.server.make_server` (Task 6); `src.config.Config.load()` (existing).
- Produces: `DashboardCommand` implementing the `Command` protocol (`src/commands/base.py`), registered in `COMMANDS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_command.py
from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from src.commands.dashboard import DashboardCommand


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    cmd = DashboardCommand()
    cmd.configure(parser)
    return parser.parse_args(argv)


def test_invalid_port_rejected():
    cmd = DashboardCommand()
    args = _parse(["--port", "0"])
    assert cmd.run(args) == 1


def test_stop_calls_daemon_uninstall():
    cmd = DashboardCommand()
    args = _parse(["--stop"])
    with patch("src.commands.dashboard.daemon.uninstall") as mock_uninstall:
        assert cmd.run(args) == 0
    mock_uninstall.assert_called_once()


def test_stop_reports_error(capsys):
    cmd = DashboardCommand()
    args = _parse(["--stop"])
    with patch("src.commands.dashboard.daemon.uninstall", side_effect=RuntimeError("boom")):
        assert cmd.run(args) == 1
    assert "boom" in capsys.readouterr().out


def test_daemon_calls_daemon_install():
    cmd = DashboardCommand()
    args = _parse(["--daemon", "--port", "8421"])
    with patch("src.commands.dashboard.daemon.install") as mock_install:
        assert cmd.run(args) == 0
    mock_install.assert_called_once_with(8421)


def test_foreground_errors_when_frontend_not_built(tmp_path, monkeypatch):
    cmd = DashboardCommand()
    monkeypatch.setattr("src.commands.dashboard._FRONTEND_DIST", tmp_path / "nope")
    args = _parse([])
    assert cmd.run(args) == 1


def test_foreground_starts_and_stops_server(tmp_path, monkeypatch):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html></html>")
    monkeypatch.setattr("src.commands.dashboard._FRONTEND_DIST", frontend)
    fake_server = MagicMock()
    fake_server.serve_forever.side_effect = KeyboardInterrupt
    with patch("src.commands.dashboard.make_server", return_value=fake_server) as mock_make:
        cmd = DashboardCommand()
        args = _parse(["--db", str(tmp_path / "usage.db")])
        assert cmd.run(args) == 0
    mock_make.assert_called_once()
    fake_server.server_close.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.commands.dashboard'`

- [ ] **Step 3: Implement**

```python
# src/commands/dashboard.py
from __future__ import annotations

import argparse
from pathlib import Path

from src.config import Config
from src.dashboard import daemon
from src.dashboard.server import make_server

_DEFAULT_PORT = 8420
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class DashboardCommand:
    name = "dashboard"
    help = "run the local usage dashboard"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--port", type=int, default=_DEFAULT_PORT,
                             help=f"port to bind (default: {_DEFAULT_PORT})")
        parser.add_argument("--daemon", action="store_true",
                             help="install a persistent background dashboard service")
        parser.add_argument("--stop", action="store_true",
                             help="remove the persistent dashboard service")

    def run(self, args: argparse.Namespace) -> int:
        if not (1 <= args.port <= 65535):
            print(f"Error: --port must be between 1 and 65535, got {args.port}")
            return 1

        if args.stop:
            try:
                daemon.uninstall()
            except RuntimeError as exc:
                print(f"Error: {exc}")
                return 1
            print("Dashboard daemon stopped.")
            return 0

        if args.daemon:
            try:
                daemon.install(args.port)
            except RuntimeError as exc:
                print(f"Error: {exc}")
                return 1
            print(f"Dashboard daemon installed — will run at "
                  f"http://127.0.0.1:{args.port} on login.")
            return 0

        if not _FRONTEND_DIST.exists():
            print(f"Error: frontend build not found at {_FRONTEND_DIST}. "
                  f"Run: cd frontend && npm install && npm run build")
            return 1

        cfg = Config.load()
        db_path = Path(args.db) if args.db else cfg.db_path
        server = make_server(db_path, _FRONTEND_DIST, args.port)
        print(f"Dashboard running at http://127.0.0.1:{args.port} (Ctrl-C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
```

```python
# src/commands/__init__.py — add DashboardCommand to the registry
from src.commands.base import Command
from src.commands.collect import CollectCommand
from src.commands.config import ConfigCommand
from src.commands.dashboard import DashboardCommand
from src.commands.projects import ProjectsCommand
from src.commands.report import ReportCommand
from src.commands.sync import SyncCommand

COMMANDS: list[Command] = [
    CollectCommand(),
    ReportCommand(),
    ConfigCommand(),
    ProjectsCommand(),
    SyncCommand(),
    DashboardCommand(),
]

__all__ = ["Command", "COMMANDS"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_command.py tests/test_tracker_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/commands/dashboard.py src/commands/__init__.py tests/test_dashboard_command.py
git commit -m "feat: add tokentracer dashboard command"
```

---

### Task 9: Frontend scaffold (Vite + React, API client)

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.jsx`
- Create: `frontend/src/api.js`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `api.js` exports `getSummary`, `getHeatmap`, `getTrend`, `getProjects`, `getProjectDetail`, `getSyncStatus`, `getMeta` — each `async (params) => json`, calling `fetch('/api/...')` relative to the page origin (works both via `vite dev` proxy and the built static serve).

- [ ] **Step 1: Add frontend build output to `.gitignore`**

```
# Frontend build (generated via `cd frontend && npm install && npm run build`)
frontend/node_modules/
frontend/dist/
```

- [ ] **Step 2: Create `frontend/package.json`**

```json
{
  "name": "tokentracer-dashboard",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 3: Create `frontend/vite.config.js`**

```javascript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8420",
    },
  },
});
```

- [ ] **Step 4: Create `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>TokenTracer Dashboard</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `frontend/src/main.jsx`**

```jsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 6: Create `frontend/src/api.js`**

```javascript
async function getJSON(path, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null),
  ).toString();
  const url = query ? `${path}?${query}` : path;
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `request failed: ${res.status}`);
  }
  return res.json();
}

export const getSummary = (params) => getJSON("/api/summary", params);
export const getHeatmap = (days) => getJSON("/api/heatmap", { days });
export const getTrend = (days) => getJSON("/api/trend", { days });
export const getProjects = (params) => getJSON("/api/projects", params);
export const getProjectDetail = (project, params) =>
  getJSON("/api/projects/detail", { ...params, project });
export const getSyncStatus = () => getJSON("/api/sync-status");
export const getMeta = () => getJSON("/api/meta");
```

- [ ] **Step 7: Verify the dev server boots** (manual, no automated test — no Node test runner in this repo)

Run:
```bash
cd frontend && npm install && npm run dev
```
Expected: Vite prints a local dev URL; visiting it shows a blank page with no console errors about missing `App.jsx` module resolution failing silently (it will 404 until Task 10 adds `App.jsx` — confirm the *dev server itself* starts without error).

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/vite.config.js frontend/index.html frontend/src/main.jsx frontend/src/api.js .gitignore
git commit -m "feat: scaffold dashboard frontend (Vite + React) and API client"
```

---

### Task 10: `App.jsx` — tab navigation + theme toggle

**Files:**
- Create: `frontend/src/App.jsx`
- Create: `frontend/src/App.css`

**Interfaces:**
- Consumes: nothing yet (pages are stubbed in this task, filled in by Tasks 10–15).
- Produces: `App` default export — renders a sidebar with "Tokens"/"By Project" nav items, a light/dark theme toggle (applies a `data-theme` attribute on `<html>`, no backend involvement), and a "Most recent data: {timestamp}" header populated from `getMeta()`.

- [ ] **Step 1: Create `frontend/src/App.jsx`**

```jsx
import { useEffect, useState } from "react";
import { getMeta } from "./api.js";
import TokensPage from "./pages/TokensPage.jsx";
import ProjectsPage from "./pages/ProjectsPage.jsx";
import "./App.css";

export default function App() {
  const [page, setPage] = useState("tokens");
  const [theme, setTheme] = useState("dark");
  const [mostRecent, setMostRecent] = useState(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    getMeta().then((data) => setMostRecent(data.most_recent_data_at)).catch(() => {});
  }, [page]);

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="nav-group">General</div>
        <button
          className={page === "tokens" ? "nav-item active" : "nav-item"}
          onClick={() => setPage("tokens")}
        >
          Tokens
        </button>
        <button
          className={page === "projects" ? "nav-item active" : "nav-item"}
          onClick={() => setPage("projects")}
        >
          By Project
        </button>
        <button className="theme-toggle" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
      </nav>
      <main className="content">
        <header className="page-header">
          <span>Most recent data: {mostRecent || "—"}</span>
        </header>
        {page === "tokens" ? <TokensPage /> : <ProjectsPage />}
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/src/App.css`**

```css
:root[data-theme="dark"] {
  --bg: #12141a; --card-bg: #1b1e27; --text: #e6e8ef; --border: #2a2e3a;
}
:root[data-theme="light"] {
  --bg: #f5f6f8; --card-bg: #ffffff; --text: #1a1c22; --border: #dfe1e6;
}
body { margin: 0; background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; }
.app { display: flex; min-height: 100vh; }
.sidebar { width: 200px; padding: 16px; border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
.nav-group { font-size: 12px; text-transform: uppercase; opacity: 0.6; margin-bottom: 8px; }
.nav-item, .theme-toggle { background: none; border: none; color: var(--text); text-align: left; padding: 8px 12px; border-radius: 6px; cursor: pointer; }
.nav-item.active { background: var(--card-bg); font-weight: 600; }
.theme-toggle { margin-top: auto; border: 1px solid var(--border); }
.content { flex: 1; padding: 24px; }
.page-header { display: flex; justify-content: flex-end; font-size: 13px; opacity: 0.7; margin-bottom: 16px; }
.card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 16px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
```

- [ ] **Step 3: Stub the two page components so the app compiles**

```jsx
// frontend/src/pages/TokensPage.jsx (stub — filled in by Tasks 10-14)
export default function TokensPage() {
  return <div className="card">Tokens page (under construction)</div>;
}
```

```jsx
// frontend/src/pages/ProjectsPage.jsx (stub — filled in by Task 16)
export default function ProjectsPage() {
  return <div className="card">Projects page (under construction)</div>;
}
```

- [ ] **Step 4: Verify manually**

Run: `cd frontend && npm run dev`, open the printed URL.
Expected: Sidebar with Tokens/By Project nav and a theme toggle renders; clicking nav items swaps the stub content; no console errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/App.css frontend/src/pages/TokensPage.jsx frontend/src/pages/ProjectsPage.jsx
git commit -m "feat: add dashboard app shell with nav and theme toggle"
```

---

### Task 11: Tokens page — stats card + sync log card

**Files:**
- Modify: `frontend/src/pages/TokensPage.jsx`
- Create: `frontend/src/components/StatsCard.jsx`
- Create: `frontend/src/components/SyncLogCard.jsx`

**Interfaces:**
- Consumes: `getSummary`, `getSyncStatus` from `api.js`.
- Produces: `StatsCard` (props: `{ summary }`) renders total/active-days/top-models; `SyncLogCard` (no props, fetches its own data) renders "Last collected: {timestamp | Never}" plus each configured store's last-synced time or "Never synced".

- [ ] **Step 1: Create `frontend/src/components/StatsCard.jsx`**

```jsx
export default function StatsCard({ summary }) {
  if (!summary) return <div className="card">Loading…</div>;
  return (
    <div className="card">
      <div className="stats-pills">
        <div className="pill"><span>{summary.total_tokens.toLocaleString()}</span><label>Total tokens</label></div>
        <div className="pill"><span>{summary.session_count}</span><label>Sessions</label></div>
        <div className="pill"><span>{summary.active_days}</span><label>Active days</label></div>
      </div>
      <div className="top-models">
        <h4>Top models</h4>
        <ol>
          {summary.models.slice(0, 5).map((m) => (
            <li key={m.model}>
              {m.model} — {(m.pct * 100).toFixed(1)}%
            </li>
          ))}
        </ol>
      </div>
      {summary.first_date && <footer>Started {summary.first_date}</footer>}
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/src/components/SyncLogCard.jsx`**

```jsx
import { useEffect, useState } from "react";
import { getSyncStatus } from "../api.js";

export default function SyncLogCard() {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    getSyncStatus()
      .then(setStatus)
      .catch(() => setStatus({ last_collected_at: null, stores: [] }));
  }, []);

  if (status === null) return <div className="card">Loading sync status…</div>;

  return (
    <div className="card">
      <h4>Sync log</h4>
      <p>Last collected: {status.last_collected_at || "Never"}</p>
      {status.stores.length === 0 ? (
        <p>No remote stores configured.</p>
      ) : (
        <ul>
          {status.stores.map((s) => (
            <li key={s.name}>{s.name}: last synced {s.last_synced_at || "never"}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Wire both into `TokensPage.jsx`**

```jsx
// frontend/src/pages/TokensPage.jsx
import { useEffect, useState } from "react";
import { getSummary } from "../api.js";
import StatsCard from "../components/StatsCard.jsx";
import SyncLogCard from "../components/SyncLogCard.jsx";

export default function TokensPage() {
  const [summary, setSummary] = useState(null);

  const refresh = () => getSummary({ period: "all" }).then(setSummary).catch(() => {});

  useEffect(() => {
    refresh();
  }, []);

  return (
    <div>
      <button onClick={refresh}>Refresh</button>
      <div className="grid-2">
        <StatsCard summary={summary} />
        <SyncLogCard />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Verify manually**

Run: `cd frontend && npm run dev` (backend running via `python3 tracker.py dashboard` in another terminal against a seeded `usage.db`).
Expected: Stats card shows real totals/top-models; sync-log card shows configured stores or the empty-state message; Refresh re-fetches.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/TokensPage.jsx frontend/src/components/StatsCard.jsx frontend/src/components/SyncLogCard.jsx
git commit -m "feat: add dashboard stats and sync-log cards"
```

---

### Task 12: Activity heatmap component

**Files:**
- Create: `frontend/src/components/Heatmap.jsx`
- Modify: `frontend/src/pages/TokensPage.jsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `getHeatmap` from `api.js`.
- Produces: `Heatmap` (no props) — fetches 180 days of data, renders a CSS-grid calendar of colored cells (color intensity scaled by `tokens / max(tokens)` across the fetched range).

- [ ] **Step 1: Create `frontend/src/components/Heatmap.jsx`**

```jsx
import { useEffect, useState } from "react";
import { getHeatmap } from "../api.js";

export default function Heatmap() {
  const [days, setDays] = useState([]);

  useEffect(() => {
    getHeatmap(180).then(setDays).catch(() => setDays([]));
  }, []);

  const max = days.reduce((m, d) => Math.max(m, d.tokens), 0);

  return (
    <div className="card">
      <h4>Activity (last 180 days)</h4>
      <div className="heatmap-grid">
        {days.map((d) => {
          const intensity = max ? d.tokens / max : 0;
          return (
            <div
              key={d.date}
              title={`${d.date}: ${d.tokens.toLocaleString()} tokens`}
              className="heatmap-cell"
              style={{ opacity: 0.15 + intensity * 0.85 }}
            />
          );
        })}
      </div>
      <div className="heatmap-legend">Less → More</div>
    </div>
  );
}
```

- [ ] **Step 2: Add heatmap styles to `frontend/src/App.css`**

```css
.heatmap-grid { display: grid; grid-template-columns: repeat(auto-fill, 10px); gap: 3px; }
.heatmap-cell { width: 10px; height: 10px; border-radius: 2px; background: #3fb950; }
.heatmap-legend { font-size: 11px; opacity: 0.6; margin-top: 8px; }
```

- [ ] **Step 3: Add `Heatmap` to `TokensPage.jsx`**

```jsx
// frontend/src/pages/TokensPage.jsx — add import and render
import Heatmap from "../components/Heatmap.jsx";
// ...inside the returned JSX, after the grid-2 div:
      <Heatmap />
```

- [ ] **Step 4: Verify manually**

Run: `npm run dev`. Expected: a grid of cells appears below the stats/sync cards, with darker cells on days with more usage; hovering a cell shows a tooltip with date + token count.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Heatmap.jsx frontend/src/pages/TokensPage.jsx frontend/src/App.css
git commit -m "feat: add dashboard activity heatmap"
```

---

### Task 13: 30-day usage trend chart

**Files:**
- Create: `frontend/src/components/TrendChart.jsx`
- Modify: `frontend/src/pages/TokensPage.jsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `getTrend` from `api.js`.
- Produces: `TrendChart` (no props) — fetches 30 days of `{date, source, tokens}`, renders a stacked-bar SVG (one bar per date, segments stacked by source, hand-rolled — no charting library) with a legend listing each source's share of the total.

- [ ] **Step 1: Create `frontend/src/components/TrendChart.jsx`**

```jsx
import { useEffect, useState } from "react";
import { getTrend } from "../api.js";

const COLORS = ["#5b8def", "#f2994a", "#9b59b6", "#27ae60", "#e74c3c", "#f1c40f"];

export default function TrendChart() {
  const [rows, setRows] = useState([]);

  useEffect(() => {
    getTrend(30).then(setRows).catch(() => setRows([]));
  }, []);

  const dates = [...new Set(rows.map((r) => r.date))].sort();
  const sources = [...new Set(rows.map((r) => r.source))];
  const byDate = Object.fromEntries(dates.map((d) => [d, {}]));
  rows.forEach((r) => { byDate[r.date][r.source] = r.tokens; });
  const dayTotals = dates.map((d) => sources.reduce((sum, s) => sum + (byDate[d][s] || 0), 0));
  const maxTotal = Math.max(1, ...dayTotals);

  const sourceTotals = Object.fromEntries(sources.map((s) => [s, 0]));
  rows.forEach((r) => { sourceTotals[r.source] += r.tokens; });
  const grandTotal = Object.values(sourceTotals).reduce((a, b) => a + b, 0) || 1;

  const barWidth = 8;
  const gap = 4;
  const chartHeight = 120;

  return (
    <div className="card">
      <h4>Usage trend (last 30 days)</h4>
      <svg width={dates.length * (barWidth + gap)} height={chartHeight}>
        {dates.map((date, i) => {
          let yOffset = chartHeight;
          return sources.map((source, si) => {
            const tokens = byDate[date][source] || 0;
            const h = (tokens / maxTotal) * chartHeight;
            yOffset -= h;
            return (
              <rect
                key={`${date}-${source}`}
                x={i * (barWidth + gap)}
                y={yOffset}
                width={barWidth}
                height={h}
                fill={COLORS[si % COLORS.length]}
              >
                <title>{`${date} — ${source}: ${tokens.toLocaleString()}`}</title>
              </rect>
            );
          });
        })}
      </svg>
      <div className="legend">
        {sources.map((s, i) => (
          <span key={s} className="legend-item">
            <i style={{ background: COLORS[i % COLORS.length] }} />
            {s}: {((sourceTotals[s] / grandTotal) * 100).toFixed(1)}%
          </span>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add legend styles to `frontend/src/App.css`**

```css
.legend { display: flex; gap: 16px; margin-top: 8px; font-size: 12px; flex-wrap: wrap; }
.legend-item { display: flex; align-items: center; gap: 4px; }
.legend-item i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
```

- [ ] **Step 3: Add `TrendChart` to `TokensPage.jsx`**

```jsx
// frontend/src/pages/TokensPage.jsx
import TrendChart from "../components/TrendChart.jsx";
// ...after <Heatmap />:
      <TrendChart />
```

- [ ] **Step 4: Verify manually**

Run: `npm run dev`. Expected: a stacked bar chart renders below the heatmap, one bar per day, segments colored per source, legend shows each source's overall percentage share.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TrendChart.jsx frontend/src/pages/TokensPage.jsx frontend/src/App.css
git commit -m "feat: add dashboard 30-day usage trend chart"
```

---

### Task 14: Total-tokens card with range tabs + harness cards

**Files:**
- Create: `frontend/src/components/HarnessCards.jsx`
- Modify: `frontend/src/pages/TokensPage.jsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `getSummary` from `api.js`.
- Produces: `HarnessCards` (props: `{ summary }`) renders a segmented progress bar of harness share plus one card per `source` present in `summary.harnesses` (never a hardcoded harness list). Range tabs (Day/Week/Month/Total/Custom) live in `TokensPage.jsx` and change the `period` param passed to `getSummary`; "Custom" reveals two date inputs that populate `start`/`end`.

- [ ] **Step 1: Create `frontend/src/components/HarnessCards.jsx`**

```jsx
export default function HarnessCards({ summary }) {
  if (!summary) return null;
  return (
    <div>
      <div className="segmented-bar">
        {summary.harnesses.map((h) => (
          <div key={h.source} className="segment" style={{ width: `${h.pct * 100}%` }} />
        ))}
      </div>
      <div className="harness-grid">
        {summary.harnesses.map((h) => (
          <div key={h.source} className="harness-card">
            <strong>{h.source}</strong>
            <span>{(h.pct * 100).toFixed(1)}%</span>
            <small>{h.model_count} model{h.model_count === 1 ? "" : "s"}</small>
          </div>
        ))}
        {summary.harnesses.length === 0 && <p>No usage data for this range.</p>}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add styles to `frontend/src/App.css`**

```css
.segmented-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--border); margin-bottom: 12px; }
.segment { background: #5b8def; border-right: 1px solid var(--bg); }
.harness-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 12px; }
.harness-card { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 4px; }
.range-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.range-tabs button { padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border); background: none; color: var(--text); cursor: pointer; }
.range-tabs button.active { background: var(--card-bg); font-weight: 600; }
```

- [ ] **Step 3: Add range tabs + `HarnessCards` to `TokensPage.jsx`**

```jsx
// frontend/src/pages/TokensPage.jsx — full file after this task
import { useEffect, useState } from "react";
import { getSummary } from "../api.js";
import StatsCard from "../components/StatsCard.jsx";
import SyncLogCard from "../components/SyncLogCard.jsx";
import Heatmap from "../components/Heatmap.jsx";
import TrendChart from "../components/TrendChart.jsx";
import HarnessCards from "../components/HarnessCards.jsx";

const RANGES = ["day", "week", "month", "all", "custom"];
const RANGE_LABELS = { day: "Day", week: "Week", month: "Month", all: "Total", custom: "Custom" };

export default function TokensPage() {
  const [summary, setSummary] = useState(null);
  const [range, setRange] = useState("all");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");

  const refresh = () => {
    const params = range === "custom"
      ? { period: "custom", start: customStart, end: customEnd }
      : { period: range };
    return getSummary(params).then(setSummary).catch(() => {});
  };

  useEffect(() => {
    if (range !== "custom" || (customStart && customEnd)) refresh();
  }, [range, customStart, customEnd]);

  return (
    <div>
      <button onClick={refresh}>Refresh</button>
      <div className="grid-2">
        <StatsCard summary={summary} />
        <SyncLogCard />
      </div>
      <Heatmap />
      <TrendChart />
      <div className="card">
        <div className="range-tabs">
          {RANGES.map((r) => (
            <button key={r} className={r === range ? "active" : ""} onClick={() => setRange(r)}>
              {RANGE_LABELS[r]}
            </button>
          ))}
        </div>
        {range === "custom" && (
          <div className="custom-range">
            <input type="date" value={customStart} onChange={(e) => setCustomStart(e.target.value)} />
            <input type="date" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
          </div>
        )}
        <h3>{summary ? summary.total_tokens.toLocaleString() : "—"} tokens</h3>
        <HarnessCards summary={summary} />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Verify manually**

Run: `npm run dev`. Expected: clicking Day/Week/Month/Total re-fetches and updates the total + harness cards; Custom reveals two date inputs and re-fetches once both are set; harness cards match exactly the sources present in `usage.db` (2 today: `claude_cli`, `copilot_cli` — never a fixed list of 8).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/HarnessCards.jsx frontend/src/pages/TokensPage.jsx frontend/src/App.css
git commit -m "feat: add dashboard range tabs and harness breakdown cards"
```

---

### Task 15: Context breakdown + model breakdown table

**Files:**
- Create: `frontend/src/components/ContextBreakdown.jsx`
- Create: `frontend/src/components/ModelTable.jsx`
- Modify: `frontend/src/pages/TokensPage.jsx`

**Interfaces:**
- Consumes: `summary` object (already fetched in `TokensPage.jsx`) — no new API calls.
- Produces: `ContextBreakdown` (props: `{ summary }`) — bar list over exactly Input/Output/Cache Read/Cache Creation/Reasoning. `ModelTable` (props: `{ summary }`) — sorted table of model/tokens/pct with an inline bar.

- [ ] **Step 1: Create `frontend/src/components/ContextBreakdown.jsx`**

```jsx
export default function ContextBreakdown({ summary }) {
  if (!summary) return null;
  const categories = [
    { label: "Input", value: summary.input_tokens },
    { label: "Output", value: summary.output_tokens },
    { label: "Cache Read", value: summary.cache_read_tokens },
    { label: "Cache Creation", value: summary.cache_creation_tokens },
    { label: "Reasoning", value: summary.reasoning_tokens },
  ];
  const total = categories.reduce((sum, c) => sum + c.value, 0) || 1;

  return (
    <div className="card">
      <h4>Context breakdown</h4>
      {categories.map((c) => (
        <div key={c.label} className="bar-row">
          <span className="bar-label">{c.label}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(c.value / total) * 100}%` }} />
          </div>
          <span className="bar-value">{c.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/src/components/ModelTable.jsx`**

```jsx
export default function ModelTable({ summary }) {
  if (!summary) return null;
  return (
    <div className="card">
      <h4>Model breakdown</h4>
      <table className="model-table">
        <thead>
          <tr><th>Model</th><th>Tokens</th><th>Share</th></tr>
        </thead>
        <tbody>
          {summary.models.map((m) => (
            <tr key={m.model}>
              <td>{m.model}</td>
              <td>{m.tokens.toLocaleString()}</td>
              <td>
                <div className="bar-track small">
                  <div className="bar-fill" style={{ width: `${m.pct * 100}%` }} />
                </div>
                {(m.pct * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Add bar/table styles to `frontend/src/App.css`**

```css
.bar-row { display: grid; grid-template-columns: 110px 1fr 70px; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; }
.bar-track { height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
.bar-track.small { display: inline-block; width: 60px; margin-right: 6px; vertical-align: middle; }
.bar-fill { height: 100%; background: #5b8def; }
.model-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.model-table th, .model-table td { text-align: left; padding: 6px 4px; border-bottom: 1px solid var(--border); }
```

- [ ] **Step 4: Add both to `TokensPage.jsx`**

```jsx
// frontend/src/pages/TokensPage.jsx
import ContextBreakdown from "../components/ContextBreakdown.jsx";
import ModelTable from "../components/ModelTable.jsx";
// ...after </div> closing the HarnessCards-containing card:
      <ContextBreakdown summary={summary} />
      <ModelTable summary={summary} />
```

- [ ] **Step 5: Verify manually**

Run: `npm run dev`. Expected: Context breakdown shows exactly 5 bars (Input/Output/Cache Read/Cache Creation/Reasoning) summing to 100%; model table lists real canonical model names sorted by token share.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ContextBreakdown.jsx frontend/src/components/ModelTable.jsx frontend/src/pages/TokensPage.jsx frontend/src/App.css
git commit -m "feat: add dashboard context-breakdown and model-breakdown views"
```

---

### Task 16: Projects page

**Files:**
- Modify: `frontend/src/pages/ProjectsPage.jsx`
- Create: `frontend/src/components/ProjectList.jsx`

**Interfaces:**
- Consumes: `getProjects`, `getProjectDetail` from `api.js`; reuses `HarnessCards` and `ContextBreakdown` from Tasks 14–15 (already accept a `summary`-shaped prop).
- Produces: `ProjectList` (props: `{ projects, selected, onSelect }`) — sorted list, click to select. `ProjectsPage` fetches the project list, tracks the selected project, and fetches/reuses `HarnessCards`/`ContextBreakdown` scoped to it via `getProjectDetail`.

- [ ] **Step 1: Create `frontend/src/components/ProjectList.jsx`**

```jsx
export default function ProjectList({ projects, selected, onSelect }) {
  const max = Math.max(1, ...projects.map((p) => p.tokens));
  return (
    <div className="card">
      <h4>Projects</h4>
      <ul className="project-list">
        {projects.map((p) => (
          <li
            key={p.project}
            className={p.project === selected ? "selected" : ""}
            onClick={() => onSelect(p.project)}
          >
            <span>{p.project}</span>
            <div className="bar-track"><div className="bar-fill" style={{ width: `${(p.tokens / max) * 100}%` }} /></div>
            <small>{p.tokens.toLocaleString()}</small>
          </li>
        ))}
        {projects.length === 0 && <p>No project data yet.</p>}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Add list styles to `frontend/src/App.css`**

```css
.project-list { list-style: none; padding: 0; margin: 0; }
.project-list li { padding: 8px; border-radius: 6px; cursor: pointer; display: grid; grid-template-columns: 1fr 100px 60px; gap: 8px; align-items: center; }
.project-list li.selected { background: var(--bg); font-weight: 600; }
```

- [ ] **Step 3: Implement `ProjectsPage.jsx`**

```jsx
// frontend/src/pages/ProjectsPage.jsx
import { useEffect, useState } from "react";
import { getProjects, getProjectDetail } from "../api.js";
import ProjectList from "../components/ProjectList.jsx";
import HarnessCards from "../components/HarnessCards.jsx";
import ContextBreakdown from "../components/ContextBreakdown.jsx";

export default function ProjectsPage() {
  const [projects, setProjects] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);

  const refreshList = () => {
    getProjects({ period: "all" }).then((data) => {
      setProjects(data);
      if (!selected && data.length > 0) setSelected(data[0].project);
    }).catch(() => {});
  };

  useEffect(() => { refreshList(); }, []);

  useEffect(() => {
    if (!selected) return;
    getProjectDetail(selected, { period: "all" }).then(setDetail).catch(() => setDetail(null));
  }, [selected]);

  return (
    <div>
      <button onClick={refreshList}>Refresh</button>
      <div className="grid-2">
        <ProjectList projects={projects} selected={selected} onSelect={setSelected} />
        <div>
          {selected ? (
            <div className="card">
              <h4>{selected}</h4>
              <p>{detail ? detail.total_tokens.toLocaleString() : "—"} tokens</p>
              <HarnessCards summary={detail} />
            </div>
          ) : (
            <div className="card">Select a project.</div>
          )}
          <ContextBreakdown summary={detail} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Verify manually**

Run: `npm run dev`, click "By Project" in the sidebar.
Expected: project list sorted by tokens descending; selecting a project updates its harness cards and context breakdown; Refresh re-fetches the list.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ProjectsPage.jsx frontend/src/components/ProjectList.jsx frontend/src/App.css
git commit -m "feat: add dashboard projects page"
```

---

### Task 17: README + full-stack verification

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md` (Commands section)

**Interfaces:** None new — this task documents Tasks 1–16 and does an end-to-end smoke test.

- [ ] **Step 1: Add dashboard usage to `README.md`**

Add a "Dashboard" section documenting:

```markdown
## Dashboard

A local web dashboard visualizes `usage.db` without the CLI `report` commands.

One-time frontend build:
\`\`\`bash
cd frontend && npm install && npm run build
\`\`\`

Then:
\`\`\`bash
tokentracer dashboard              # foreground, http://127.0.0.1:8420, Ctrl-C to stop
tokentracer dashboard --port 9000  # custom port
tokentracer dashboard --daemon     # install as a persistent background service (survives reboot/logout)
tokentracer dashboard --stop       # remove the persistent service
\`\`\`
```

- [ ] **Step 2: Add the `dashboard` command to `CLAUDE.md`'s Commands section**

```markdown
# Run the local dashboard (after building the frontend once, see README)
python3 tracker.py dashboard
python3 tracker.py dashboard --daemon
python3 tracker.py dashboard --stop
```

- [ ] **Step 3: Run the full backend test suite**

Run: `python3 -m pytest -q`
Expected: all tests pass, including the 4 new `test_dashboard_*.py` files from Tasks 1–3 and 5–8, plus the new `tests/test_sqlite_run_log.py` and the `CollectCommand` wiring test from Task 4.

- [ ] **Step 4: End-to-end manual smoke test**

```bash
cd frontend && npm install && npm run build && cd ..
python3 tracker.py collect --lookback 3
python3 tracker.py dashboard
```
Open `http://127.0.0.1:8420` in a browser. Verify: Tokens page loads with real totals, heatmap, trend chart, harness cards matching actual collected sources, context breakdown, and model table; switching to the Projects page and selecting a project updates its detail view; theme toggle works; Refresh buttons re-fetch without a full page reload.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document tokentracer dashboard command"
```

## Out of scope (matches spec)

- Authentication or non-loopback network exposure.
- Auto-refresh/polling.
- System-prompt, subagent/skill, and MCP-server-specific token breakdowns (see spec's Out of Scope section).
- A `dashboard --status` command.
- Linux/systemd daemon support.
SPEC_EOF
