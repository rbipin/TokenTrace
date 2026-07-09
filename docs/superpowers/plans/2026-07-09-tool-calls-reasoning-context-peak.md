# Tool Calls, Reasoning Tokens & Context Peak Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `tool_calls` and `context_peak_tokens` from both collectors, surface `Reasoning`/`Tools`/`CtxPeak` in the detailed report, add a `report --detailed` full-dump view with sync status, and push the missing columns to remote stores.

**Architecture:** Collectors gain in-pass counters (no new I/O for Claude; one extra bulk query for Copilot's `assistant_usage_events` table). The Copilot event scan no longer short-circuits on `session.shutdown` because tool-call counts exist only as discrete events. Reporting follows the existing `ReportStrategy` pattern — the detailed view gains columns and a new `FullDumpView` strategy backs `--detailed`. `SupabaseStore` payload adds the three omitted fields.

**Tech Stack:** Python 3 stdlib only (sqlite3, json, dataclasses). pytest for tests.

**Specs:**
- `docs/superpowers/specs/2026-07-09-tool-calls-reasoning-tokens-design.md`
- `docs/superpowers/specs/2026-07-09-context-peak-tokens-design.md`

## Global Constraints

- Collectors are **read-only** with respect to source files — never write to `~/.copilot` or `~/.claude`.
- `collect` stays idempotent: last-write-wins upsert on `(session_id, source, model)`. No summation across runs.
- No third-party runtime deps. `supabase` stays an optional extra behind try/except import.
- No SQLite schema changes — `tool_calls`, `reasoning_tokens`, `context_peak_tokens` columns already exist.
- No backfill tooling — re-running `collect --lookback N` overwrites rows.
- Run tests with `python -m pytest <file> -q` from the repo root `C:\Repo\me\TokenTrace`.
- Commit messages: conventional-commit style, no Co-authored-by trailer.

---

### Task 1: Copilot collector — count tool calls per model

**Files:**
- Modify: `src/collectors/copilot_cli.py` (`_parse_events`, `_from_shutdown`)
- Test: `tests/test_cli_collector.py`

**Interfaces:**
- Consumes: existing `CopilotCliCollector._parse_events(session_id, date_str, start_ts, end_ts, project)` and `_from_shutdown(payload, session_id, date_str, start_ts, end_ts, project)`.
- Produces: `_parse_events(...)` unchanged signature; `_from_shutdown(payload, session_id, date_str, start_ts, end_ts, project, tool_calls_by_model: dict[str, int])` — Task 2 extends both with a `peaks` parameter, keep this ordering (`tool_calls_by_model` before `peaks`).
- Behavioral contract: `SessionRecord.tool_calls` is the count of `tool.execution_complete` events; per-model when a `session.shutdown` event exists, summed total otherwise.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_collector.py`:

```python
def _tool_event(model: str | None = None) -> dict:
    e: dict = {"type": "tool.execution_complete"}
    if model:
        e["model"] = model
    return e


def test_tool_calls_counted_per_model_with_shutdown(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _tool_event("model-a"),
        _tool_event("model-a"),
        _tool_event("model-b"),
        _shutdown({
            "model-a": {"turns": 2, "input": 100, "output": 10},
            "model-b": {"turns": 1, "input": 50, "output": 5},
        }),
    ])
    records = {r.model: r for r in CopilotCliCollector(home).collect(date(2026, 6, 10))}
    assert records["model-a"].tool_calls == 2
    assert records["model-b"].tool_calls == 1


def test_tool_calls_summed_in_fallback_without_shutdown(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        {"type": "assistant.message", "model": "model-a",
         "usage": {"inputTokens": 10, "outputTokens": 5}},
        _tool_event("model-a"),
        _tool_event(),  # no model field — still counted in the session total
    ])
    records = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))
    assert len(records) == 1
    assert records[0].tool_calls == 2


def test_tool_calls_zero_when_no_tool_events(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"model-a": {"turns": 1, "input": 10, "output": 1}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.tool_calls == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_collector.py -q -k tool_calls`
Expected: 3 FAIL (tool_calls is 0 everywhere; `_from_shutdown` lacks the parameter only after implementation — failures here are assertion errors).

- [ ] **Step 3: Implement**

In `src/collectors/copilot_cli.py`, replace the body of `_parse_events` from the `totals = ...` line down with:

```python
        # Single pass: prefer the shutdown event (per-model breakdown), while
        # accumulating assistant-message totals as the fallback. Tool calls
        # exist only as discrete events, so the scan always runs to the end.
        totals = dict(input_tokens=0, output_tokens=0, cache_read_tokens=0,
                      cache_creation_tokens=0, reasoning_tokens=0)
        turns = 0
        model = UNKNOWN_MODEL
        tool_calls_by_model: dict[str, int] = {}
        shutdown_payload: dict | None = None

        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            payload = event.get("data") or event  # new CLI nests under "data"
            if event_type == "session.shutdown":
                shutdown_payload = payload
                continue
            if event_type == "tool.execution_complete":
                tool_model = payload.get("model") or model
                tool_calls_by_model[tool_model] = tool_calls_by_model.get(tool_model, 0) + 1
                continue
            if event_type != "assistant.message":
                continue
            turns += 1
            if model == UNKNOWN_MODEL and payload.get("model"):
                model = payload["model"]
            usage = payload.get("usage") or {}
            totals["input_tokens"] += usage.get("inputTokens", 0)
            totals["output_tokens"] += usage.get(
                "outputTokens", payload.get("outputTokens", 0)
            )
            totals["cache_read_tokens"] += usage.get("cacheReadTokens", 0)
            totals["cache_creation_tokens"] += usage.get("cacheWriteTokens", 0)
            totals["reasoning_tokens"] += usage.get("reasoningTokens", 0)

        if shutdown_payload is not None:
            yield from self._from_shutdown(
                shutdown_payload, session_id, date_str, start_ts, end_ts,
                project, tool_calls_by_model,
            )
            return

        yield SessionRecord(
            session_id=session_id,
            source=self.source,
            model=model,
            date=date_str,
            start_ts=start_ts,
            end_ts=end_ts,
            project=project,
            turns=turns,
            tool_calls=sum(tool_calls_by_model.values()),
            **totals,
        )
```

Change `_from_shutdown` to accept and use the counts:

```python
    def _from_shutdown(
        self, payload: dict, session_id: str,
        date_str: str, start_ts: str | None, end_ts: str | None,
        project: str | None, tool_calls_by_model: dict[str, int],
    ) -> Iterator[SessionRecord]:
        metrics: dict = payload.get("modelMetrics") or {}
        for model, m in metrics.items():
            # Old format: token counts flat on the metric dict, plus "turns".
            # New format: counts nested under "usage", turns under requests.count.
            usage = m.get("usage") or m
            turns = m.get("turns") or (m.get("requests") or {}).get("count", 0)
            yield SessionRecord(
                session_id=session_id,
                source=self.source,
                model=model or UNKNOWN_MODEL,
                date=date_str,
                start_ts=start_ts,
                end_ts=end_ts,
                project=project,
                turns=turns,
                tool_calls=tool_calls_by_model.get(model, 0),
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                cache_read_tokens=usage.get("cacheReadTokens", 0),
                cache_creation_tokens=usage.get("cacheWriteTokens", 0),
                reasoning_tokens=usage.get("reasoningTokens", 0),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_collector.py -q`
Expected: all PASS (new tests plus the entire existing file — the shutdown restructure must not break existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/collectors/copilot_cli.py tests/test_cli_collector.py
git commit -m "feat: count Copilot tool.execution_complete events into tool_calls"
```

---

### Task 2: Copilot collector — context peak from assistant_usage_events

**Files:**
- Modify: `src/collectors/copilot_cli.py` (`collect`, `_parse_events`, `_from_shutdown`, new `_query_context_peaks`)
- Test: `tests/test_cli_collector.py`

**Interfaces:**
- Consumes: Task 1's `_parse_events(...)` / `_from_shutdown(..., tool_calls_by_model)`.
- Produces: `_query_context_peaks(conn) -> dict[tuple[str, str], int]` (staticmethod); `_parse_events(session_id, date_str, start_ts, end_ts, project, peaks)` and `_from_shutdown(payload, session_id, date_str, start_ts, end_ts, project, tool_calls_by_model, peaks)` where `peaks` maps `(session_id, model)` → max single-request footprint.
- Behavioral contract: `SessionRecord.context_peak_tokens = MAX(input_tokens + output_tokens)` over `assistant_usage_events` rows with `agent_id IS NULL` (Copilot's `input_tokens` is cache-inclusive); `0` when the table is absent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_collector.py`:

```python
def _add_usage_events(home: Path, rows: list[tuple]) -> None:
    """rows: (session_id, agent_id, model, input_tokens, output_tokens)"""
    conn = sqlite3.connect(home / "session-store.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_usage_events (
            session_id TEXT, turn_index INTEGER, agent_id TEXT, model TEXT,
            input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER,
            reasoning_tokens INTEGER
        )
    """)
    conn.executemany(
        "INSERT INTO assistant_usage_events VALUES (?, 0, ?, ?, ?, ?, 0, 0, 0)",
        rows,
    )
    conn.commit()
    conn.close()


def test_context_peak_from_usage_events(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _add_usage_events(home, [
        ("s1", None, "model-a", 1000, 50),   # footprint 1050
        ("s1", None, "model-a", 2000, 100),  # footprint 2100 <- peak
        ("s1", None, "model-a", 500, 20),
    ])
    _write_events(home, "s1", [
        _shutdown({"model-a": {"turns": 3, "input": 3500, "output": 170}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.context_peak_tokens == 2100


def test_context_peak_excludes_subagent_rows(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _add_usage_events(home, [
        ("s1", None, "model-a", 1000, 50),          # main: 1050 <- peak
        ("s1", "agent-123", "model-a", 9000, 900),  # subagent: excluded
    ])
    _write_events(home, "s1", [
        _shutdown({"model-a": {"turns": 1, "input": 1000, "output": 50}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.context_peak_tokens == 1050


def test_context_peak_zero_when_table_missing(tmp_path):
    home = _make_home(tmp_path)  # fixture db has no assistant_usage_events table
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"model-a": {"turns": 1, "input": 10, "output": 1}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.context_peak_tokens == 0


def test_context_peak_in_fallback_path(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/x",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T13:00:00.000Z")
    _add_usage_events(home, [("s1", None, "model-a", 700, 30)])
    _write_events(home, "s1", [
        {"type": "assistant.message", "model": "model-a",
         "usage": {"inputTokens": 700, "outputTokens": 30}},
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.context_peak_tokens == 730
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_collector.py -q -k context_peak`
Expected: FAIL — `context_peak_tokens == 0` assertions on the first, second, and fourth tests (third passes trivially; that's fine, it guards the regression).

- [ ] **Step 3: Implement**

In `src/collectors/copilot_cli.py`:

In `collect()`, load peaks while the connection is open (peaks failure must not block session reads):

```python
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = self._query_sessions(conn)
            peaks = self._query_context_peaks(conn)
        except sqlite3.OperationalError as exc:
            print(
                f"Warning [copilot_cli]: could not read sessions table: {exc}",
                file=sys.stderr,
            )
            return
        finally:
            conn.close()
```

At the end of `collect()`'s loop pass peaks through:

```python
            yield from self._parse_events(
                session_id, date_str, start_iso, end_iso, project, peaks
            )
```

Add the staticmethod (it swallows its own OperationalError, so a missing table never aborts collection):

```python
    @staticmethod
    def _query_context_peaks(
        conn: sqlite3.Connection,
    ) -> dict[tuple[str, str], int]:
        """Max single-request footprint per (session, model), main conversation only.

        Copilot's assistant_usage_events.input_tokens already includes cache
        read + cache write, so the footprint is input + output. Returns an
        empty dict when the table doesn't exist (older CLI versions).
        """
        try:
            rows = conn.execute("""
                SELECT session_id, model,
                       MAX(input_tokens + output_tokens) AS peak
                FROM assistant_usage_events
                WHERE agent_id IS NULL
                GROUP BY session_id, model
            """).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {(r["session_id"], r["model"]): r["peak"] or 0 for r in rows}
```

Extend `_parse_events` signature with `peaks: dict[tuple[str, str], int]` (after `project`), pass `peaks` on to `_from_shutdown` (after `tool_calls_by_model`), and add to the fallback `SessionRecord`:

```python
            context_peak_tokens=peaks.get((session_id, model), 0),
```

Extend `_from_shutdown` with `peaks: dict[tuple[str, str], int]` (after `tool_calls_by_model`) and add to each yielded record:

```python
                context_peak_tokens=peaks.get((session_id, model), 0),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_collector.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/collectors/copilot_cli.py tests/test_cli_collector.py
git commit -m "feat: capture context peak tokens from Copilot assistant_usage_events"
```

---

### Task 3: Claude collector — tool_use count and context peak

**Files:**
- Modify: `src/collectors/claude_cli.py` (`_parse_session`)
- Test: `tests/test_claude_cli_collector.py`

**Interfaces:**
- Consumes: existing `_parse_session(path, since) -> SessionRecord | None` internals.
- Produces: records with `tool_calls` = count of `tool_use` content blocks across assistant messages, and `context_peak_tokens` = max per-message `input_tokens + cache_read_input_tokens + cache_creation_input_tokens + output_tokens`. `reasoning_tokens` stays `0` (Claude usage has no such field).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_cli_collector.py`:

```python
def test_tool_calls_counted_from_tool_use_blocks(tmp_path):
    e1 = _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50)
    e1["message"]["content"] = [
        {"type": "text", "text": "let me look"},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
        {"type": "tool_use", "id": "t2", "name": "Grep", "input": {}},
    ]
    e2 = _asst("2026-07-03T10:05:00.000Z", "claude-sonnet-4-6", 100, 20)
    e2["message"]["content"] = [
        {"type": "tool_use", "id": "t3", "name": "Edit", "input": {}},
    ]
    _write_session(tmp_path, "sess-tools", [e1, e2])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.tool_calls == 3


def test_string_content_not_counted_and_no_crash(tmp_path):
    e = _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 100, 50)
    e["message"]["content"] = "plain string reply"
    _write_session(tmp_path, "sess-str", [e])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.tool_calls == 0


def test_context_peak_is_max_message_footprint(tmp_path):
    _write_session(tmp_path, "sess-peak", [
        # footprint = 10 + 40000 + 500 + 100 = 40610
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 100,
              cache_create=500, cache_read=40000),
        # footprint = 20 + 44000 + 600 + 90 = 44710  <- peak
        _asst("2026-07-03T10:05:00.000Z", "claude-sonnet-4-6", 20, 90,
              cache_create=600, cache_read=44000),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.context_peak_tokens == 44710


def test_reasoning_tokens_stay_zero(tmp_path):
    _write_session(tmp_path, "sess-r", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5),
    ])
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.reasoning_tokens == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_claude_cli_collector.py -q -k "tool_use or footprint or string_content"`
Expected: `test_tool_calls_counted_from_tool_use_blocks` and `test_context_peak_is_max_message_footprint` FAIL (values 0); the other two pass trivially as regression guards.

- [ ] **Step 3: Implement**

In `src/collectors/claude_cli.py`, in `_parse_session` add two locals next to `turns = 0`:

```python
        tool_calls = 0
        context_peak = 0
```

At the end of the assistant-message branch (after the existing `totals[...]` lines), append:

```python
            footprint = (
                usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("output_tokens", 0)
            )
            if footprint > context_peak:
                context_peak = footprint
            content = msg.get("content") or []
            if isinstance(content, list):
                tool_calls += sum(
                    1 for block in content
                    if isinstance(block, dict) and block.get("type") == "tool_use"
                )
```

Add both fields to the returned `SessionRecord` (after `turns=turns,`):

```python
            tool_calls=tool_calls,
            context_peak_tokens=context_peak,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_claude_cli_collector.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/collectors/claude_cli.py tests/test_claude_cli_collector.py
git commit -m "feat: count tool_use blocks and track context peak in Claude collector"
```

---

### Task 4: Supabase payload completeness

**Files:**
- Modify: `src/stores/supabase.py` (`upsert` row dict)
- Test: `tests/test_supabase_store.py`

**Interfaces:**
- Consumes: `SessionRecord` fields `tool_calls`, `reasoning_tokens`, `context_peak_tokens` (all pre-existing on the dataclass).
- Produces: Supabase upsert rows containing keys `tool_calls`, `reasoning_tokens`, `context_peak_tokens`. Conflict key `session_id,source,model` unchanged. Remote `token_sessions` tables need matching integer columns (documented in Task 7).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_supabase_store.py`:

```python
def test_upsert_includes_tool_and_token_detail_columns():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        rec = SessionRecord(
            session_id="s1", source="copilot_cli", model="m",
            date="2026-07-09", turns=3, tool_calls=7,
            input_tokens=100, output_tokens=50,
            reasoning_tokens=42, context_peak_tokens=2100,
        )
        store.upsert([rec])
    rows = mock_client.table.return_value.upsert.call_args[0][0]
    assert rows[0]["tool_calls"] == 7
    assert rows[0]["reasoning_tokens"] == 42
    assert rows[0]["context_peak_tokens"] == 2100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_supabase_store.py -q -k detail_columns`
Expected: FAIL with `KeyError: 'tool_calls'`.

- [ ] **Step 3: Implement**

In `src/stores/supabase.py`, in the `upsert` row dict, after `"turns": r.turns,` add:

```python
                "tool_calls": r.tool_calls,
```

and after `"cache_read_tokens": r.cache_read_tokens,` add:

```python
                "context_peak_tokens": r.context_peak_tokens,
                "reasoning_tokens": r.reasoning_tokens,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_supabase_store.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stores/supabase.py tests/test_supabase_store.py
git commit -m "feat: push tool_calls, reasoning and context peak tokens to Supabase"
```

---

### Task 5: Detailed session view — Reasoning, Tools, CtxPeak columns

**Files:**
- Modify: `src/report.py` (`SessionsDetailedView.render`)
- Test: `tests/test_store_report.py`

**Interfaces:**
- Consumes: `sessions` columns `reasoning_tokens`, `tool_calls`, `context_peak_tokens`; `UsageStore` (alias of `SqliteStore`) for fixtures; `UsageReporter(db_path).report(...)`.
- Produces: default detailed view header order `Project Source Model Start End Input Output Reasoning CacheRead CacheCreate CacheHit% CtxPeak Turns Tools`; JSON rows include `reasoning_tokens`, `tool_calls`, `context_peak_tokens`. Summary/by-project views unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store_report.py` (module already imports `UsageReporter` at line 83; `_rec` helper and `tmp_db` fixture exist):

```python
def test_detailed_view_shows_reasoning_tools_ctxpeak(tmp_db):
    from datetime import date as _date
    store = UsageStore(tmp_db)
    store.upsert([_rec("s-cols", date_str=_date.today().isoformat(),
                       reasoning_tokens=42, tool_calls=7,
                       context_peak_tokens=2100, input_tokens=100)])
    out = UsageReporter(tmp_db).report(period="day")
    assert "Reasoning" in out
    assert "Tools" in out
    assert "CtxPeak" in out
    assert "42" in out
    assert "2100" in out


def test_detailed_json_includes_new_fields(tmp_db):
    import json as _json
    from datetime import date as _date
    store = UsageStore(tmp_db)
    store.upsert([_rec("s-json", date_str=_date.today().isoformat(),
                       reasoning_tokens=9, tool_calls=3,
                       context_peak_tokens=500)])
    payload = _json.loads(UsageReporter(tmp_db).report(period="day", as_json=True))
    row = payload["rows"][0]
    assert row["reasoning_tokens"] == 9
    assert row["tool_calls"] == 3
    assert row["context_peak_tokens"] == 500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_store_report.py -q -k "reasoning_tools or new_fields"`
Expected: 2 FAIL (`"Reasoning" not in out`, `KeyError: 'reasoning_tokens'`).

- [ ] **Step 3: Implement**

In `src/report.py`, `SessionsDetailedView.render`: add to the SELECT list (after `cache_creation_tokens,`):

```sql
                reasoning_tokens,
                tool_calls,
                context_peak_tokens,
```

Replace the `table_rows.append([...])` block with:

```python
            table_rows.append([
                r["project"],
                r["source"],
                r["model"],
                (r["start_ts"] or "")[:19],
                (r["end_ts"] or "")[:19],
                r["input_tokens"],
                r["output_tokens"],
                r["reasoning_tokens"],
                r["cache_read_tokens"],
                r["cache_creation_tokens"],
                cache_pct,
                r["context_peak_tokens"],
                r["turns"],
                r["tool_calls"],
            ])
```

and the headers with:

```python
            headers=["Project", "Source", "Model", "Start", "End",
                     "Input", "Output", "Reasoning", "CacheRead", "CacheCreate",
                     "CacheHit%", "CtxPeak", "Turns", "Tools"],
```

(The JSON branch needs no change — it serializes all selected columns via `dict(r)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_store_report.py tests/test_pipeline.py -q`
Expected: all PASS (`test_pipeline.py` renders the detailed view end-to-end, so it must survive the column change).

- [ ] **Step 5: Commit**

```bash
git add src/report.py tests/test_store_report.py
git commit -m "feat: surface Reasoning, Tools and CtxPeak columns in detailed report"
```

---

### Task 6: `report --detailed` full-dump view

**Files:**
- Modify: `src/report.py` (new `FullDumpView`, `_pick_strategy`, `UsageReporter.report`)
- Modify: `src/commands/report.py` (flag + pass-through)
- Test: `tests/test_store_report.py`

**Interfaces:**
- Consumes: `sessions` + `sync_log` tables; `ReportContext` (uses `model_filter`/`params`/`as_json`; ignores `date_filter`); `SqliteStore.mark_synced(records, store_name)` for test fixtures.
- Produces: `FullDumpView` strategy class; `_pick_strategy(summary, by_project, period, detailed=False)`; `UsageReporter.report(..., detailed: bool = False)`; CLI flag `--detailed` on the `report` command. `--detailed` takes precedence over `--summary`/`--by-project` and ignores `--period`; `--model` still applies.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store_report.py`:

```python
def test_detailed_flag_dumps_all_rows_all_columns(tmp_db):
    store = UsageStore(tmp_db)
    old = _rec("s-old", date_str="2020-01-01", tool_calls=1)
    new = _rec("s-new", date_str="2026-07-09", reasoning_tokens=5)
    store.upsert([old, new])
    store.mark_synced([old], "supabase")
    out = UsageReporter(tmp_db).report(detailed=True)
    # all rows regardless of date
    assert "s-old" in out
    assert "s-new" in out
    # all columns, including context and sync status
    for header in ("Session", "Source", "Model", "Date", "Start", "End",
                   "Project", "Turns", "Tools", "Input", "Output",
                   "CacheCreate", "CacheRead", "CtxPeak", "Reasoning",
                   "Context", "Synced"):
        assert header in out
    assert "supabase" in out  # s-old synced marker


def test_detailed_flag_json_and_model_filter(tmp_db):
    import json as _json
    store = UsageStore(tmp_db)
    store.upsert([
        _rec("s1", model="model-a", tool_calls=2),
        _rec("s2", model="model-b"),
    ])
    payload = _json.loads(
        UsageReporter(tmp_db).report(detailed=True, models=["model-a"], as_json=True)
    )
    rows = payload["rows"]
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"
    assert rows[0]["tool_calls"] == 2
    assert rows[0]["synced"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_store_report.py -q -k detailed_flag`
Expected: FAIL with `TypeError: report() got an unexpected keyword argument 'detailed'`.

- [ ] **Step 3: Implement**

In `src/report.py`, add after `SessionsListView` (before the Dispatch section):

```python
class FullDumpView:
    """--detailed: every row in the db, every column, plus sync status."""

    def render(self, ctx: ReportContext) -> str:
        # No date filter by design: --detailed always dumps the whole table.
        # sync_log is folded in via a correlated subquery to avoid aliasing
        # the sessions table (ctx.model_filter references bare column names).
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
                    SELECT GROUP_CONCAT(l.store_name, ',')
                    FROM sync_log l
                    WHERE l.session_id = sessions.session_id
                      AND l.source = sessions.source
                      AND l.model = sessions.model
                ), '')                  AS synced
            FROM sessions
            WHERE 1=1{ctx.model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, ctx.params).fetchall()

        if ctx.as_json:
            return json.dumps({
                "cache_efficiency": {
                    "hit_rate": round(ctx.hit_rate, 4),
                    "cost_saved_rate": round(ctx.cost_saved, 4),
                },
                "rows": [dict(r) for r in rows],
            }, indent=2)

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

Update `_pick_strategy`:

```python
def _pick_strategy(
    summary: bool, by_project: bool, period: str, detailed: bool = False
) -> ReportStrategy:
    """Select the correct render strategy from the dispatch axes."""
    if detailed:
        return FullDumpView()
    if by_project:
        return ByProjectView()
    if not summary:
        return SessionsDetailedView()
    if period in ("all", "month", "year"):
        return PeriodSummaryView()
    return SessionsListView()
```

Update `UsageReporter.report` signature and dispatch:

```python
    def report(
        self,
        period: str = "day",
        models: Sequence[str] | None = None,
        by_project: bool = False,
        summary: bool = False,
        as_json: bool = False,
        detailed: bool = False,
    ) -> str:
        if period not in _PERIOD_SQL:
            raise ValueError(f"period must be one of {list(_PERIOD_SQL)}")
        conn = self._connect()
        try:
            ctx = self._make_context(conn, period, models, as_json)
            return _pick_strategy(summary, by_project, period, detailed).render(ctx)
        finally:
            conn.close()
```

In `src/commands/report.py`, add to `configure`:

```python
        parser.add_argument("--detailed", action="store_true",
                            help="dump every row in the db with all columns "
                                 "and sync status (ignores --period)")
```

and pass it through in `run`:

```python
        output = reporter.report(
            period=args.period,
            models=args.model or None,
            by_project=args.by_project,
            summary=args.summary,
            as_json=args.json,
            detailed=args.detailed,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_store_report.py tests/test_tracker_cli.py -q`
Expected: all PASS.

- [ ] **Step 5: Smoke-test the CLI**

Run: `python tracker.py report --detailed` (from `C:\Repo\me\TokenTrace`, uses default db)
Expected: table with all 17 headers; exit code 0. Then `python tracker.py report --detailed --json | python -c "import json,sys; json.load(sys.stdin); print('ok')"` prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add src/report.py src/commands/report.py tests/test_store_report.py
git commit -m "feat: add report --detailed full-dump view with sync status"
```

---

### Task 7: Documentation updates

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: final behavior from Tasks 1–6.
- Produces: docs consistent with shipped behavior. No tests (docs only).

- [ ] **Step 1: Update README.md**

- In the report/usage examples section, add:

  ```bash
  # Dump every row in the db with all columns and sync status
  python3 tracker.py report --detailed
  ```

- In the feature list, mention tool-call and context-peak tracking (one line each).
- In the Supabase section, note that the remote `token_sessions` table needs integer columns `tool_calls`, `reasoning_tokens`, and `context_peak_tokens` (e.g. `ALTER TABLE token_sessions ADD COLUMN tool_calls integer DEFAULT 0;` and likewise for the other two).

- [ ] **Step 2: Update CLAUDE.md**

- Commands block: add `python3 tracker.py report --detailed   # all rows, all columns, sync status` after the other report examples.
- Copilot data-details paragraph: state that `tool.execution_complete` events are counted into `tool_calls` (per model via the event's `model` field when a shutdown event exists, summed otherwise), that the event scan no longer stops at `session.shutdown`, and that `context_peak_tokens` comes from a bulk `MAX(input_tokens + output_tokens)` query over `assistant_usage_events` with `agent_id IS NULL` (cache-inclusive `input_tokens`; missing table → 0).
- Claude data-details paragraph: state that `tool_use` content blocks are counted into `tool_calls`, that `context_peak_tokens` is the max per-message `input + cache_read + cache_creation + output`, and that `reasoning_tokens` stays 0 (no source field).
- Supabase paragraph: note the payload now includes `tool_calls`, `reasoning_tokens`, `context_peak_tokens` and the remote table needs those columns.
- Report section in the architecture map: mention `--detailed` and the new columns in the default view.

- [ ] **Step 3: Update docs/ARCHITECTURE.md**

Apply the same behavior updates where the corresponding components are described (collectors, reporter strategies, Supabase store). Add a short subsection on `context_peak_tokens` semantics: `peak = MAX(prompt + output)` per request, subagent requests excluded, exact API-reported values, 0 when unavailable.

- [ ] **Step 4: Full test suite as a final gate**

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md docs/ARCHITECTURE.md
git commit -m "docs: document tool_calls, context peak and report --detailed"
```
