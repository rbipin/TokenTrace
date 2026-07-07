# Pluggable Store Interface — Design

**Status:** Approved  
**Date:** 2026-07-06

---

## Overview

A pluggable storage interface for TokenTracer that allows session records to be pushed to multiple backends (SQLite, Supabase, Cosmos DB, SQL, etc.) based on configuration. SQLite remains the always-on local store; remote stores are opt-in push destinations.

---

## Decisions

| Topic | Decision |
|-------|----------|
| Multi-store writes | Pipeline writes to all stores simultaneously |
| Default store | SQLite always active, always first, always consistent |
| Sync model | Records pushed during `collect` AND separately via `tokentracer sync` |
| Where implementations live | Hybrid: built-in stores ship in-repo; entry points for plug-and-play; `class =` escape hatch for power users |
| Error handling | Log and continue — remote failures warn but don't abort collect |
| Credentials | Config file only (`[stores.X]` section in `~/.tokentracer.toml`) |
| Sync state tracking | `sync_log` table: `(session_id, source, model, store_name, synced_at)` |

---

## `SessionStore` Protocol

```python
class SessionStore(Protocol):
    name: str  # matches config key; used as sync_log store_name

    def upsert(self, records: list[SessionRecord]) -> int: ...
    def close(self) -> None: ...
```

- Duck-typed — no subclassing required, mirrors `ActivityCollector`
- `name` is the config key and sync_log foreign key
- `close()` flushes buffers / closes connections — called by both the pipeline and the `sync` command after each run
- Existing `UsageStore` is refactored to satisfy this Protocol (`name = "sqlite"`)

---

## Discovery & Registration

Stores are discovered via Python entry points under group `tokentracer.stores`:

```python
from importlib.metadata import entry_points

def load_store_registry() -> dict[str, type]:
    return {
        ep.name: ep.load()
        for ep in entry_points(group="tokentracer.stores")
    }
```

Built-in stores self-register in `pyproject.toml`:

```toml
[project.entry-points."tokentracer.stores"]
sqlite = "tokentracer.stores.sqlite:SqliteStore"
```

Third-party packages do the same — `pip install tokentracer-supabase` makes `supabase` available as a store name automatically.

**Config activation:**

```toml
# sqlite is always active — no section needed

[stores.supabase]           # resolved via entry point
url = "https://..."
api_key = "..."

[stores.mystore]            # class= bypasses registry (power user escape hatch)
class = "mypackage.MyStore"
endpoint = "..."
```

---

## Pipeline Changes

`TrackerPipeline.stores()` replaces the single-store `.store()` method:

```python
def stores(self, *stores: SessionStore) -> "TrackerPipeline":
    self._stores = list(stores)
    return self
```

In `run()`, after `merge_records`:

```python
# SQLite always first — must succeed
written = self._stores[0].upsert(merged)

# Remotes in parallel — log-and-continue on failure
def _push(store: SessionStore) -> str | None:
    try:
        store.upsert(merged)
        return None
    except Exception as exc:
        return f"{store.name}: {exc}"

with ThreadPoolExecutor() as pool:
    for err in pool.map(_push, self._stores[1:]):
        if err:
            errors.append(f"[store] {err}")
```

`RunResult` gains `stores_failed: list[str]` to surface remote warnings without aborting.

The existing `.store(single)` method is kept as a deprecated alias to avoid call-site breakage.

---

## `sync_log` Table & `sync` Command

**Schema** (lives in `SqliteStore`):

```sql
CREATE TABLE IF NOT EXISTS sync_log (
    session_id  TEXT NOT NULL,
    source      TEXT NOT NULL,
    model       TEXT NOT NULL,
    store_name  TEXT NOT NULL,
    synced_at   TEXT NOT NULL,
    PRIMARY KEY (session_id, source, model, store_name)
);
```

**`tokentracer sync` flow:**

1. Load all configured remote stores via registry
2. For each store, query `sessions` rows with no `sync_log` entry for that store name
3. Push pending records via `store.upsert()`
4. On success, insert `sync_log` rows
5. On failure, log warning and continue — unlogged records retry on next `sync`

**Output:**

```
Syncing 3 stores...
  supabase   42 records pushed
  cosmos      0 pending
  mystore    failed: connection timeout (12 records pending)
```

`--dry-run` flag shows pending counts without pushing.

---

## File Map

| File | Change |
|------|--------|
| `src/stores/__init__.py` | New — exports `SessionStore` Protocol |
| `src/stores/sqlite.py` | New — `SqliteStore` refactored from `src/store.py` |
| `src/stores/registry.py` | New — `load_store_registry()` via entry points |
| `src/store.py` | Kept as deprecated shim re-exporting `SqliteStore` |
| `src/pipeline.py` | `.stores(*stores)` replaces `.store(store)`; parallel remote push |
| `src/config.py` | `[stores]` section parsing; per-store config dict passed to constructors |
| `tracker.py` | Wire `sync` subcommand; pass stores list to pipeline |
| `pyproject.toml` | Register `sqlite` entry point under `tokentracer.stores` |

---

## Post-Implementation

After all changes are implemented, update:
- `README.md` — document the `[stores]` config section, entry point plugin model, and `sync` command
- `CLAUDE.md` — update architecture section and commands table to reflect the new store layer and `sync` subcommand

---

## Adding a New Built-in Store

1. Create `src/stores/<name>.py` implementing `SessionStore`
2. Register in `pyproject.toml` under `[project.entry-points."tokentracer.stores"]`
3. Document required config keys in the store's docstring
4. Add tests under `tests/stores/` using `tmp_path` / mock HTTP

No other files need to change.
