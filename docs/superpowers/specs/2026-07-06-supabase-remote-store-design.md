# Supabase Remote Store — Design Spec

**Date:** 2026-07-06  
**Branch:** remote-db  
**Status:** Approved

---

## Overview

Add a `SupabaseStore` class that implements the existing `SessionStore` Protocol so TokenTracer can sync local session records to a Supabase table via the `tokentracer sync` command. No changes to the sync pipeline, collectors, or CLI are needed — the store plugs in through the existing entry-point registry.

---

## Architecture

**New file:** `src/stores/supabase.py` — single `SupabaseStore` class.

**Modified files:**
- `src/config.py` — add `_expand_env_vars(params: dict) -> dict` helper; call it in `instantiate_store()`.
- `src/stores/__init__.py` — export `SupabaseStore`.
- `pyproject.toml` — add `supabase` entry point under `tokentracer.stores`.
- `pyproject.toml` — add `supabase-py` as an optional dependency (e.g., `extras = ["supabase"]`).

**No changes** to `tracker.py`, `pipeline.py`, `sqlite.py`, or any collector.

---

## `SupabaseStore` Class

```python
class SupabaseStore:
    source = "supabase"

    def __init__(self, url: str, key: str, table: str = "token_sessions") -> None: ...
    @property
    def _client(self) -> Client: ...   # lazy, cached supabase-py client
    def upsert(self, records: list[SessionRecord]) -> int: ...
    def close(self) -> None: ...
```

### Behaviour

- **`__init__`**: stores `url`, `key`, `table`. No network call.
- **`_client` (property)**: creates `supabase.create_client(url, key)` on first access; caches in `self._client_cache`. Subsequent calls return the cache.
- **`upsert`**: serializes each `SessionRecord` to a dict and calls:
  ```python
  self._client.table(self.table).upsert(rows, on_conflict="session_id,source,model").execute()
  ```
  Returns `len(records)` as the count (supabase-py's execute response does not expose a row count; since upsert is idempotent this equals the number of records submitted). Conflict key matches the local SQLite primary key — re-running sync is idempotent.
- **`close`**: sets `self._client_cache = None` (supabase-py has no explicit close method).

---

## Supabase Table Schema

Create once in Supabase. Table name: **`token_sessions`** (configurable via `table` param).

| Column | Type | Notes |
|---|---|---|
| `session_id` | `text` | |
| `source` | `text` | e.g. `"claude"`, `"copilot"` |
| `model` | `text` | |
| `date` | `date` | |
| `start_ts` | `timestamptz` | |
| `end_ts` | `timestamptz` | |
| `project` | `text` | nullable |
| `turns` | `int4` | |
| `input_tokens` | `int8` | |
| `output_tokens` | `int8` | |
| `cache_creation_tokens` | `int8` | |
| `cache_read_tokens` | `int8` | |
| PRIMARY KEY | `(session_id, source, model)` | matches local SQLite key |

Auth: **service role key** — bypasses RLS, full write access. Appropriate for a personal sync tool.

---

## Configuration

### `.tokentracer.toml`

```toml
[stores.supabase]
url = "${SUPABASE_URL}"
key = "${SUPABASE_KEY}"
# table = "token_sessions"   # optional override
```

### Env var expansion

`_expand_env_vars(params: dict) -> dict` in `src/config.py`:
- Walks all string values in the params dict.
- Replaces `${VAR_NAME}` with `os.environ["VAR_NAME"]`.
- Raises `ValueError("Missing env var VAR_NAME referenced in store config")` if the variable is absent.
- Called inside `instantiate_store()` before the store constructor — applies to all stores, not just Supabase.

Users source their own `.env` (via `direnv`, shell rc, or `source .env`). No auto-loading; zero extra deps beyond `supabase-py`.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Missing env var | `ValueError` raised in `instantiate_store()` before any network call; shown to user by `cmd_sync` |
| Supabase API error | Exception propagates from `upsert()` to `_run_sync()`, which catches it, marks store failed, continues with remaining stores |
| No retry logic | `_run_sync` already handles failure reporting; retries are a future concern |

---

## Testing

File: `tests/test_supabase_store.py`

- Mock `supabase.create_client` — no real network calls.
- **Serialization**: `upsert()` sends correct dict shape to the Supabase client with right table name and conflict key.
- **Idempotency**: calling `upsert()` twice with the same records produces correct upsert calls (not inserts).
- **Lazy client**: `create_client` is not called in `__init__`; only called on first `upsert()`.
- **`_expand_env_vars`** (unit tested separately):
  - Substitutes known env vars.
  - Raises `ValueError` for missing vars.
  - Passes through values with no `${}` unchanged.

---

## Dependencies

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
supabase = ["supabase>=2.0"]
```

Install with: `pip install tokentracer[supabase]` or `uv tool install .[supabase]`.
