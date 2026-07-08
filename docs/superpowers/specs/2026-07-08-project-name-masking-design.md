# Project Name Masking (tri-state `track_project_names`) — Design

## Problem

`track_project_names` is currently a boolean in `~/.tokentracer.toml`:

- `true`: collectors resolve the real project name (repository name for Copilot, `cwd` basename for Claude Code) and store it in `sessions.project`.
- `false`: `sessions.project` is left `NULL` — no project identity is recorded at all.

There is no way to get **per-project breakdowns** (`report --by-project`) without exposing the real project/repo name, and there is no way to get a **stable but privacy-preserving identifier** for a project.

## Goals

- Turn `track_project_names` into a 3-way string setting:
  - `"yes"` — real project name (today's `true` behavior).
  - `"no"` — a stable, opaque **guid** derived from the working directory, in place of `NULL`.
  - `"whimsical"` — a stable, human-friendly **docker-style masked name** (e.g. `admiring_agnesi`), derived from the same guid.
- The guid/whimsical-name mapping must be **stable per project**: the same working directory (case-insensitively) always yields the same guid, and the same guid always yields the same whimsical name.
- The mapping table is **local-only** — it must never be pushed to remote stores via `tracker.py sync`.
- Plain booleans (`true`/`false`) are **no longer accepted** for `track_project_names`; this is an intentional breaking change.

## Non-goals

- No change to the `sessions` table schema (the `project` column stays `TEXT`, just with different content).
- No retroactive re-identification of historical rows collected under the old boolean scheme.
- No UI/reporting changes beyond what already consumes `sessions.project`.

## Design

### 1. Config & CLI

- `Config.track_project_names` becomes `str`, one of `"yes"`, `"no"`, `"whimsical"`. Default: `"no"`.
- `tracker.py config set track_project_names <yes|no|whimsical>` validates against this enum (replaces the old boolean parsing).
- `collect` CLI flag becomes a single choice flag: `--project-mode {yes,no,whimsical}` (replaces the old mutually-exclusive `--track-projects` / `--no-track-projects` pair — booleans are removed, not aliased).
- Resolution order for a `collect` run: CLI flag > toml > default (`"no"`), matching today's existing override pattern.

### 2. Identity storage — `src/project_identity.py`

A new `ProjectIdentityStore`, backed by a new table in the **same `usage.db`** used by `SqliteStore` (not the `sessions` table):

```sql
CREATE TABLE IF NOT EXISTS project_identities (
    cwd_key         TEXT PRIMARY KEY,   -- normalized (case-folded, trimmed) cwd
    guid            TEXT NOT NULL UNIQUE,
    whimsical_name  TEXT UNIQUE,
    created_at      TEXT NOT NULL
)
```

- `resolve_guid(cwd: str) -> str`: normalizes `cwd` (strip + casefold) as the lookup key; if no row exists, generates a new short guid (first 12 hex chars of a `uuid4`) and inserts a row. Same cwd in any case → same guid.
- `resolve_whimsical(cwd: str) -> str`: calls `resolve_guid` first (ensuring the row exists), then returns the existing `whimsical_name` if set, or generates one via `src/whimsy.py` and persists it. Same guid → same whimsical name, always.
- This table is **never referenced** by `SqliteStore.unsynced_for` / `mark_synced` / any remote-store upsert path — it has no relationship to `sync_log` and is excluded from sync by construction (it's simply a different table that sync code never queries).
- If `cwd` is empty/unavailable, both methods return `None` — no identity is fabricated.

### 3. Whimsical name generator — `src/whimsy.py`

- Ports the adjective list (`left`) and scientist/hacker surname list (`right`) from Docker's Apache-2.0-licensed [`namesgenerator.go`](https://github.com/docker-archive/docker-ce/blob/master/components/engine/pkg/namesgenerator/names-generator.go), with a short attribution comment crediting Docker/Moby and noting the Apache 2.0 license.
- `generate_name(existing: set[str]) -> str`: picks a random `adjective_surname` combo; retries (capped, e.g. 20 attempts) on collision against `existing`; if still colliding after retries, appends an incrementing numeric suffix (Docker's own fallback behavior) until unique.
- **Supplementary surnames**: extend the ported `right` (surname) list with the notable persons listed in [`docs/superpowers/specs/new-names.txt`](./new-names.txt), which is the source of truth for these additions — each entry keeps its original one-line bio + Wikipedia link comment, ported verbatim into `whimsy.py` in the same annotated-comment style as Docker's list (do not flatten the annotations away when implementing).
  - Entries in that file already present in the ported Docker list are duplicates and must be **skipped**: `ramanujan`, `visvesvaraya`, `bhabha`, `feynman`, `torvalds`, `bardeen`, `burnell`, `moser`.

### 4. Collector integration

- `CopilotCliCollector.__init__` / `ClaudeCliCollector.__init__`: `track_project_names: bool` → `project_name_mode: str`, plus an optional `identity_store: ProjectIdentityStore | None` (required only when mode is `"no"`/`"whimsical"`).
- Resolution per record, keyed on the **raw cwd** already available in both collectors (Copilot: `row["cwd"]`; Claude: `entry.get("cwd")`) — repository name is *not* used as the identity key for `"no"`/`"whimsical"`, only for the `"yes"` display name:
  - `"yes"`: unchanged — repo name (Copilot) / cwd basename, as today.
  - `"no"`: `project = identity_store.resolve_guid(cwd)`.
  - `"whimsical"`: `project = identity_store.resolve_whimsical(cwd)`.
- Identity-store errors (e.g. locked db file) are caught and logged as a warning per collector (consistent with the existing `sqlite3.OperationalError` handling in `copilot_cli.py`), falling back to `project=None` rather than aborting collection.

### 5. Wiring — `tracker.py`

- `_build_pipeline` instantiates one shared `ProjectIdentityStore(cfg.db_path)` and passes it plus `cfg.track_project_names` into both collectors.
- `cmd_config_set` validates `track_project_names` against `{"yes", "no", "whimsical"}` instead of boolean parsing.

## Testing

- `tests/test_project_identity.py`: guid stability across case variations of the same cwd; distinct cwds → distinct guids; whimsical name stability tied to guid; concurrent/duplicate resolution is idempotent.
- `tests/test_whimsy.py`: generated names match `adjective_surname` format; collision retry produces a unique name; numeric-suffix fallback after exhausting retries.
- Update existing tests (`test_config.py`, `test_cli_collector.py`, `test_claude_cli_collector.py`, `test_config_stores.py`, `test_context.py`) for the new string-mode API.
- Regression test confirming `project_identities` rows are never included in `SqliteStore.unsynced_for(...)` output / never pushed to a remote store.

## Documentation updates

The following docs must be updated alongside the implementation, since they document the current boolean `track_project_names` behavior:

- **`README.md`**: update any `track_project_names` / `--track-projects` / `--no-track-projects` usage examples to the new `"yes"|"no"|"whimsical"` values and the `--project-mode` flag. Add a short explanation of the `"no"` (guid) and `"whimsical"` (masked name) modes and the local-only `project_identities` table.
- **`CLAUDE.md`**: update the `[tracking]` config description (currently documents `track_project_names` as a plain bool) and the `src/` architecture map to list the new `src/project_identity.py` and `src/whimsy.py` modules, following the existing "Adding a new collector" style of documentation.
- **`docs/ARCHITECTURE.md`**: update the data-flow / module description to include `ProjectIdentityStore` and the whimsical-name generator in the collection pipeline, and note that `project_identities` is local-only and excluded from sync.

## Migration / compatibility notes

- This is a breaking change to `~/.tokentracer.toml` and the `collect` CLI flags. Existing `track_project_names = true|false` entries will need to be rewritten as `"yes"|"no"` — no automatic migration is performed, per explicit user direction that booleans need not be accepted.
- Historical `sessions.project` values already collected are untouched; only new collection runs are affected.
