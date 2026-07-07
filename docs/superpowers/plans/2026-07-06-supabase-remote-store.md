# Supabase Remote Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `SupabaseStore` class that syncs local `SessionRecord`s to a Supabase `token_sessions` table, plugging into the existing `tokentracer sync` pipeline with zero changes to that pipeline.

**Architecture:** Single `SupabaseStore` class in `src/stores/supabase.py` implementing the `SessionStore` Protocol (`upsert` + `close`). Env var expansion (`${VAR_NAME}`) is added to `src/config.py` and called in `instantiate_store()` before any store constructor, so all stores benefit. The store is registered via a `pyproject.toml` entry point.

**Tech Stack:** Python 3.11+, `supabase-py >= 2.0` (optional dependency), `unittest.mock` for tests.

## Global Constraints

- Python `>= 3.11` (project minimum)
- No new runtime dependencies beyond `supabase-py` (added as optional extra `[supabase]`)
- `supabase-py` must be `>= 2.0`
- Upsert conflict key: `"session_id,source,model"` — matches local SQLite primary key
- Default table name: `"token_sessions"`
- Auth: service role key (bypasses RLS)
- `_expand_env_vars` raises `ValueError` with message `"Missing env var 'VAR_NAME' referenced in store config"` for absent variables
- No changes to `tracker.py`, `pipeline.py`, `sqlite.py`, or any collector

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/config.py` | Modify | Add `_expand_env_vars(params: dict) -> dict` |
| `src/stores/registry.py` | Modify | Call `_expand_env_vars` in `instantiate_store()` before store construction |
| `src/stores/supabase.py` | Create | `SupabaseStore` class |
| `src/stores/__init__.py` | Modify | Export `SupabaseStore` |
| `pyproject.toml` | Modify | Add `supabase` entry point + optional dep |
| `tests/test_config_stores.py` | Modify | Add `_expand_env_vars` unit tests |
| `tests/test_supabase_store.py` | Create | `SupabaseStore` unit tests |

---

### Task 1: `_expand_env_vars` helper + wire into `instantiate_store`

**Files:**
- Modify: `src/config.py`
- Modify: `src/stores/registry.py`
- Test: `tests/test_config_stores.py`

**Interfaces:**
- Produces: `_expand_env_vars(params: dict) -> dict` — public at module level in `src/config.py`
- Produces: `instantiate_store()` now expands env vars in `params` before passing to constructor

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/test_config_stores.py`:

```python
import os
import pytest
from src.config import _expand_env_vars


def test_expand_env_vars_substitutes_known_var(monkeypatch):
    monkeypatch.setenv("MY_URL", "https://example.supabase.co")
    result = _expand_env_vars({"url": "${MY_URL}", "other": "literal"})
    assert result == {"url": "https://example.supabase.co", "other": "literal"}


def test_expand_env_vars_raises_for_missing_var(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(ValueError, match="Missing env var 'MISSING_VAR'"):
        _expand_env_vars({"key": "${MISSING_VAR}"})


def test_expand_env_vars_passthrough_non_string():
    result = _expand_env_vars({"count": 42, "flag": True})
    assert result == {"count": 42, "flag": True}


def test_expand_env_vars_passthrough_no_placeholder():
    result = _expand_env_vars({"url": "https://literal.com"})
    assert result == {"url": "https://literal.com"}


def test_instantiate_store_expands_env_vars(monkeypatch, tmp_path):
    """instantiate_store passes expanded params to the store constructor."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "usage.db"))
    from src.stores.registry import instantiate_store
    store = instantiate_store(
        "sqlite",
        {"db_path": "${SQLITE_PATH}"},
        class_path="src.stores.sqlite.SqliteStore",
    )
    store.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_config_stores.py::test_expand_env_vars_substitutes_known_var tests/test_config_stores.py::test_expand_env_vars_raises_for_missing_var tests/test_config_stores.py::test_expand_env_vars_passthrough_non_string tests/test_config_stores.py::test_expand_env_vars_passthrough_no_placeholder tests/test_config_stores.py::test_instantiate_store_expands_env_vars -v
```

Expected: `ImportError: cannot import name '_expand_env_vars' from 'src.config'`

- [ ] **Step 3: Add `_expand_env_vars` to `src/config.py`**

Add after the existing imports at the top of `src/config.py` (after `from pathlib import Path`):

```python
import os
import re
```

Add this function before the `StoreConfig` dataclass definition:

```python
def _expand_env_vars(params: dict) -> dict:
    """Replace ${VAR_NAME} placeholders in string values with os.environ lookups."""
    pattern = re.compile(r"\$\{([^}]+)\}")
    result: dict = {}
    for key, value in params.items():
        if isinstance(value, str):
            def _replace(m: re.Match, _params_key=key) -> str:
                var = m.group(1)
                if var not in os.environ:
                    raise ValueError(
                        f"Missing env var {var!r} referenced in store config"
                    )
                return os.environ[var]
            result[key] = pattern.sub(_replace, value)
        else:
            result[key] = value
    return result
```

- [ ] **Step 4: Wire `_expand_env_vars` into `instantiate_store` in `src/stores/registry.py`**

Add this import at the top of `src/stores/registry.py` (after the existing imports):

```python
from ..config import _expand_env_vars
```

In `instantiate_store()`, add the expansion call as the **first line of the function body** (before the `if class_path is not None:` block):

```python
def instantiate_store(
    name: str,
    params: dict,
    class_path: str | None = None,
) -> "SessionStore":
    params = _expand_env_vars(params)
    # ... rest of existing body unchanged ...
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_config_stores.py -v
```

Expected: all tests in the file pass, including the five new ones.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/config.py src/stores/registry.py tests/test_config_stores.py
git commit -m "feat: add env var expansion for store params"
```

---

### Task 2: `SupabaseStore` implementation

**Files:**
- Create: `src/stores/supabase.py`
- Create: `tests/test_supabase_store.py`

**Interfaces:**
- Consumes: `SessionRecord` from `src.models` (fields: `session_id`, `source`, `model`, `date`, `start_ts`, `end_ts`, `project`, `turns`, `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`)
- Produces: `SupabaseStore(url: str, key: str, table: str = "token_sessions")` — implements `SessionStore` Protocol

- [ ] **Step 1: Write the failing tests**

Create `tests/test_supabase_store.py`:

```python
"""Tests for SupabaseStore."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.models import SessionRecord
from src.stores.supabase import SupabaseStore


def _rec(sid: str = "s1", source: str = "claude", model: str = "claude-sonnet-4-6") -> SessionRecord:
    return SessionRecord(
        session_id=sid,
        source=source,
        model=model,
        date="2026-07-01",
        start_ts="2026-07-01T10:00:00",
        end_ts="2026-07-01T11:00:00",
        project="myproject",
        turns=5,
        input_tokens=100,
        output_tokens=200,
        cache_creation_tokens=10,
        cache_read_tokens=20,
    )


def _make_store(url="https://x.supabase.co", key="service-role-secret", table="token_sessions"):
    return SupabaseStore(url=url, key=key, table=table)


def test_init_does_not_call_create_client():
    with patch("src.stores.supabase._create_client") as mock_create:
        _make_store()
        mock_create.assert_not_called()


def test_client_created_on_first_upsert():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client) as mock_create:
        store = _make_store(url="https://x.supabase.co", key="my-key")
        store.upsert([_rec()])
        mock_create.assert_called_once_with("https://x.supabase.co", "my-key")


def test_client_cached_across_upserts():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client) as mock_create:
        store = _make_store()
        store.upsert([_rec("s1")])
        store.upsert([_rec("s2")])
        mock_create.assert_called_once()


def test_upsert_sends_correct_row_shape():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        store.upsert([_rec()])

    rows = mock_client.table.return_value.upsert.call_args[0][0]
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "s1"
    assert row["source"] == "claude"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["date"] == "2026-07-01"
    assert row["start_ts"] == "2026-07-01T10:00:00"
    assert row["end_ts"] == "2026-07-01T11:00:00"
    assert row["project"] == "myproject"
    assert row["turns"] == 5
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 200
    assert row["cache_creation_tokens"] == 10
    assert row["cache_read_tokens"] == 20


def test_upsert_uses_conflict_key():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        store.upsert([_rec()])

    kwargs = mock_client.table.return_value.upsert.call_args[1]
    assert kwargs.get("on_conflict") == "session_id,source,model"


def test_upsert_targets_correct_table():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store(table="custom_table")
        store.upsert([_rec()])

    mock_client.table.assert_called_with("custom_table")


def test_upsert_returns_record_count():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        result = store.upsert([_rec("s1"), _rec("s2")])

    assert result == 2


def test_upsert_empty_returns_zero_without_network_call():
    with patch("src.stores.supabase._create_client") as mock_create:
        store = _make_store()
        result = store.upsert([])
        assert result == 0
        mock_create.assert_not_called()


def test_close_clears_client_cache():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        store.upsert([_rec()])
        assert store._client_cache is not None
        store.close()
        assert store._client_cache is None


def test_name_attribute():
    store = _make_store()
    assert store.name == "supabase"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_supabase_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.stores.supabase'`

- [ ] **Step 3: Create `src/stores/supabase.py`**

```python
"""Supabase remote store for TokenTracer."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import SessionRecord

try:
    from supabase import create_client as _create_client
except ImportError:
    _create_client = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from supabase import Client


class SupabaseStore:
    """Remote store that upserts SessionRecords into a Supabase table."""

    name = "supabase"

    def __init__(self, url: str, key: str, table: str = "token_sessions") -> None:
        self._url = url
        self._key = key
        self._table = table
        self._client_cache: Client | None = None

    @property
    def _client(self) -> "Client":
        if self._client_cache is None:
            if _create_client is None:
                raise ImportError(
                    "supabase-py is required for SupabaseStore. "
                    "Install with: pip install tokentracer[supabase]"
                )
            self._client_cache = _create_client(self._url, self._key)
        return self._client_cache

    def upsert(self, records: list[SessionRecord]) -> int:
        """Upsert records into Supabase; returns the count submitted."""
        if not records:
            return 0
        rows = [
            {
                "session_id": r.session_id,
                "source": r.source,
                "model": r.model,
                "date": r.date,
                "start_ts": r.start_ts,
                "end_ts": r.end_ts,
                "project": r.project,
                "turns": r.turns,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_creation_tokens": r.cache_creation_tokens,
                "cache_read_tokens": r.cache_read_tokens,
            }
            for r in records
        ]
        self._client.table(self._table).upsert(
            rows, on_conflict="session_id,source,model"
        ).execute()
        return len(records)

    def close(self) -> None:
        self._client_cache = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_supabase_store.py -v
```

Expected: all 11 tests pass. (Note: `supabase-py` does not need to be installed — `_create_client` is patched in every test that calls `upsert`; the import-error path is not tested here.)

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/stores/supabase.py tests/test_supabase_store.py
git commit -m "feat: add SupabaseStore remote store"
```

---

### Task 3: Registration — entry point + export

**Files:**
- Modify: `src/stores/__init__.py`
- Modify: `pyproject.toml`
- Test: verify via `test_stores_registry.py` (no new test file needed — add one test to verify the entry point name resolves correctly once installed)

**Interfaces:**
- Consumes: `SupabaseStore` from Task 2
- Produces: `from src.stores import SupabaseStore` works; `instantiate_store("supabase", {...})` resolves the class via entry points after `pip install .[supabase]`

- [ ] **Step 1: Export `SupabaseStore` from `src/stores/__init__.py`**

Open `src/stores/__init__.py`. It currently exports `SessionStore`. Add the `SupabaseStore` export:

```python
from .supabase import SupabaseStore

__all__ = ["SessionStore", "SupabaseStore"]
```

- [ ] **Step 2: Add entry point and optional dep to `pyproject.toml`**

In the `[project.entry-points."tokentracer.stores"]` section, add the `supabase` line:

```toml
[project.entry-points."tokentracer.stores"]
sqlite = "src.stores.sqlite:SqliteStore"
supabase = "src.stores.supabase:SupabaseStore"
```

Add the optional dependency section (add after `[project.scripts]`):

```toml
[project.optional-dependencies]
supabase = ["supabase>=2.0"]
```

- [ ] **Step 3: Add class-path resolution test to `tests/test_stores_registry.py`**

Add this test to `tests/test_stores_registry.py`:

```python
def test_supabase_store_instantiates_via_class_path(tmp_path):
    from src.stores.registry import instantiate_store
    from src.stores.supabase import SupabaseStore

    store = instantiate_store(
        "supabase",
        {"url": "https://x.supabase.co", "key": "secret"},
        class_path="src.stores.supabase.SupabaseStore",
    )
    assert isinstance(store, SupabaseStore)
    assert store._url == "https://x.supabase.co"
    assert store._key == "secret"
    assert store._table == "token_sessions"
    store.close()
```

- [ ] **Step 4: Run the registry tests**

```bash
python3 -m pytest tests/test_stores_registry.py -v
```

Expected: all tests pass including the new `test_supabase_store_instantiates_via_class_path`.

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/stores/__init__.py pyproject.toml tests/test_stores_registry.py
git commit -m "feat: register SupabaseStore entry point and optional dep"
```

---

## Post-Implementation: Manual Smoke Test

After all tasks are complete, do a quick manual check:

1. Add to `~/.tokentracer.toml`:
   ```toml
   [stores.supabase]
   class = "src.stores.supabase.SupabaseStore"
   url = "${SUPABASE_URL}"
   key = "${SUPABASE_KEY}"
   ```
2. Set env vars: `export SUPABASE_URL=... SUPABASE_KEY=...`
3. Run: `python3 tracker.py sync --dry-run` — should print pending counts without pushing.
4. If a real Supabase project is available, run without `--dry-run` and verify rows appear in the `token_sessions` table.

---

## Supabase Table SQL

Run once in Supabase SQL editor to create the table:

```sql
create table token_sessions (
  session_id text not null,
  source text not null,
  model text not null,
  date date,
  start_ts timestamptz,
  end_ts timestamptz,
  project text,
  turns integer default 0,
  input_tokens bigint default 0,
  output_tokens bigint default 0,
  cache_creation_tokens bigint default 0,
  cache_read_tokens bigint default 0,
  primary key (session_id, source, model)
);
```
