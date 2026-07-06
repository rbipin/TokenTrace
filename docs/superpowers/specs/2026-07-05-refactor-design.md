# TokenTracer Refactor Design

**Date:** 2026-07-05  
**Branch:** additional-data  
**Scope:** Internal restructuring only — CLI flags and output formats are frozen.

---

## Goals

- Eliminate the 7-argument parameter clump shared by all render methods in `report.py`
- Replace boolean-flag dispatch with a Strategy pattern for render modes
- Fix misleading method name (`_render_default` → `PeriodSummaryView`)
- Remove copy-paste JSON output logic
- Split the dual-path `write_toml_setting` into two focused private helpers
- Extract inline flag normalization in `tracker.py` into named helpers
- All 44 existing tests must continue to pass; no behavioral changes

---

## Files Changed

| File | Change |
|------|--------|
| `src/report.py` | Add `ReportContext`, `ReportStrategy` protocol, 4 strategy classes, `_make_context()`, `_format_output()` |
| `src/config.py` | Split `write_toml_setting` into `_write_toml_311` + `_write_toml_legacy` |
| `tracker.py` | Add `_parse_bool_arg`, extract `_build_report_args` |

`src/models.py`, `src/store.py`, `src/pipeline.py`, `src/collectors/` — untouched.

---

## Section 1: `src/report.py`

### `ReportContext` dataclass

Replaces the 7-argument data clump passed to every render method.

```python
@dataclass(frozen=True)
class ReportContext:
    conn: sqlite3.Connection
    date_filter: str        # SQL WHERE fragment, e.g. "strftime('%Y-%m', start) = ?"
    model_filter: str | None
    params: tuple           # bind parameters for date_filter (+ model if set)
    hit_rate: float         # cache hit rate 0–1
    cost_saved: float       # estimated tokens saved by cache
    as_json: bool
```

### `ReportStrategy` protocol

```python
class ReportStrategy(Protocol):
    def render(self, ctx: ReportContext) -> str | None: ...
```

### Four strategy classes

Each is a small, focused class implementing `ReportStrategy`. Internal SQL and formatting logic moves into the class body. No class carries state beyond its methods.

| Class | Replaces | When used |
|-------|----------|-----------|
| `SessionsDetailedView` | `_render_sessions_detailed` | default (no flags) |
| `PeriodSummaryView` | `_render_default` (misnamed) | `--summary` without `--by-project` |
| `SessionsListView` | `_render_sessions` | `--by-project` without `--summary` |
| `ByProjectView` | `_render_by_project` | `--summary --by-project` |

### Dispatch table

```python
_VIEWS: dict[tuple[bool, bool], ReportStrategy] = {
    (False, False): SessionsDetailedView(),
    (True, False):  PeriodSummaryView(),
    (False, True):  SessionsListView(),
    (True, True):   ByProjectView(),
}
```

### `_make_context()` factory method

Added to `UsageReporter`. Accepts the raw report args (period, model, as_json) and returns a fully constructed `ReportContext`. Centralises the SQL date-filter expression and params-tuple assembly that currently lives inline inside `report()`.

### `_format_output()` helper

Single function that replaces the copy-paste JSON/table pattern in all 4 render methods:

```python
def _format_output(rows, headers, widths, as_json: bool) -> str | None:
    if as_json:
        return json.dumps([dict(zip(headers, r)) for r in rows], indent=2)
    return _format_table(headers, rows, widths)
```

### `UsageReporter.report()` after refactor

```python
def report(self, ...) -> str | None:
    with self._connect() as conn:
        ctx = self._make_context(conn, period, model, as_json)
        strategy = _VIEWS[(summary, by_project)]
        return strategy.render(ctx)
```

---

## Section 2: `src/config.py`

### Split `write_toml_setting`

Current: one 60-line function with two deeply nested branches.

After:

```python
def write_toml_setting(key: str, value: bool) -> None:
    if tomllib is not None:
        _write_toml_311(key, value)
    else:
        _write_toml_legacy(key, value)

def _write_toml_311(key: str, value: bool) -> None:
    """Full parse-and-rewrite path (Python 3.11+)."""
    ...  # existing 3.11 logic, unchanged

def _write_toml_legacy(key: str, value: bool) -> None:
    """Line-patch fallback (Python < 3.11)."""
    ...  # existing fallback logic, unchanged
```

Logic is identical — only structure changes. Existing tests continue to cover both paths.

---

## Section 3: `tracker.py`

### `_parse_bool_arg(val: str) -> bool`

Extracts the inline truthy-string check:

```python
def _parse_bool_arg(val: str) -> bool:
    return val.lower() in ("1", "true", "yes", "on")
```

Called from the `config set` handler.

### Extract `_build_report_args`

The 15+ lines of flag normalization at the top of `cmd_report` (period defaulting, `track_projects` override resolution against TOML config) move into `_build_report_args(args, cfg) -> ReportArgs`. `cmd_report` becomes:

```python
def cmd_report(args) -> int:
    cfg = Config.load()
    report_args = _build_report_args(args, cfg)
    reporter = UsageReporter(cfg.db_path)
    output = reporter.report(**report_args)
    if output:
        print(output)
    return 0
```

---

## Testing

No new test files required. The strategy classes are internal to `report.py`; existing report tests exercise them indirectly through `UsageReporter.report()`. The refactor makes each view independently unit-testable if desired in future — each can be instantiated and called with a synthetic `ReportContext`.

`_parse_bool_arg` and `_build_report_args` are also independently testable without argparse setup.

---

## Non-Goals

- No changes to CLI flags or output formats
- No changes to `src/collectors/`, `src/store.py`, `src/models.py`, `src/pipeline.py`
- No new external dependencies
- No VS Code / Web / Desktop collectors (per existing policy)
