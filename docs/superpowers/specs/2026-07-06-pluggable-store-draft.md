# Pluggable Store Interface — Brainstorming Draft

**Status:** In progress — brainstorming not yet complete  
**Started:** 2026-07-06

---

## What we're designing

A pluggable storage interface for TokenTracer that allows session records to be pushed to multiple backends (SQLite, Supabase, Cosmos DB, SQL, etc.) based on configuration.

---

## Decisions made so far

### Multi-store writes
The pipeline writes to **multiple stores simultaneously**, not one at a time.

### SQLite as default local store
SQLite remains the always-on local store. Remote stores (Supabase, Cosmos, etc.) are opt-in push destinations. Local store is always consistent even if a remote is down or misconfigured.

### Sync model: Option C — both
- **During `collect`**: same pipeline run, records go to SQLite + remotes
- **Separate `sync` command**: `tokentracer sync` pushes from SQLite to remotes on demand (backfill / retry failed pushes)

---

## Open questions (not yet answered)

1. **Where do third-party implementations live?**
   - A) External packages only (plugin entry points)
   - B) Built-in registry (implementations ship in-repo, config activates them)
   - C) Hybrid — built-in stores ship in-repo, interface is public so external class-path implementations also work

2. **Error handling**: if a remote store fails during collect, do we log and continue or surface the error to the user?

3. **Auth/credentials for remotes**: how are connection strings / API keys provided — env vars, config file section, or keychain?

4. **Sync state tracking**: how does the `sync` command know which records haven't been pushed to a given remote yet? (e.g. a `sync_log` table, a `last_synced_at` per store, or full re-push idempotent upsert?)

---

## Relevant existing code

| File | Role |
|------|------|
| `src/store.py` | Current SQLite-only `UsageStore` — the seam where pluggability needs to live |
| `src/pipeline.py` | `TrackerPipeline.store()` accepts a single `UsageStore` — needs to accept a list of stores |
| `src/collectors/base.py` | `ActivityCollector` Protocol — good model to follow for the new `SessionStore` Protocol |
| `src/config.py` | TOML-based config — needs a `[stores]` section to activate/configure remotes |

---

## Next step when resuming

Answer open question #1 (where implementations live), then proceed to:
- Propose 2-3 interface approaches
- Design the `SessionStore` Protocol
- Design the config schema for activating stores
- Design the `sync` command
