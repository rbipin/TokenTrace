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
- `resolve_whimsical(cwd: str) -> str`: calls `resolve_guid` first (ensuring the row exists), then returns the existing `whimsical_name` if set, or generates one via `src/whimsy` (`generate_name`) and persists it. Same guid → same whimsical name, always.
- This table is **never referenced** by `SqliteStore.unsynced_for` / `mark_synced` / any remote-store upsert path — it has no relationship to `sync_log` and is excluded from sync by construction (it's simply a different table that sync code never queries).
- If `cwd` is empty/unavailable, both methods return `None` — no identity is fabricated.

### 3. Whimsical name generator — standalone component at `src/whimsy/`

This component is deliberately built as a **self-contained, dependency-free package** so it can be lifted into its own repo/project later with a simple directory copy — no other module in this codebase should import from it in a way that would need to change on extraction, and it must never import anything from `src/` itself (no `..models`, `..config`, etc.), only the Python standard library (`random`).

Layout:

```
src/whimsy/
    __init__.py       # public API: generate_name(existing: set[str]) -> str
    wordlists.py       # `ADJECTIVES` and `SURNAMES` tuples (data only)
    generator.py       # generation + collision-retry logic
    README.md          # standalone usage docs + Apache 2.0 attribution notice
    LICENSE-NOTICE.md  # attribution text for the ported Docker word lists
tests/whimsy/
    test_generator.py  # imports only from src.whimsy — no fixtures shared with the rest of the test suite
```

- `wordlists.py` ports the adjective list (`left`) and scientist/hacker surname list (`right`) from Docker's Apache-2.0-licensed [`namesgenerator.go`](https://github.com/docker-archive/docker-ce/blob/master/components/engine/pkg/namesgenerator/names-generator.go), each entry keeping its original one-line bio + Wikipedia link comment. `LICENSE-NOTICE.md` records the Apache 2.0 attribution required for the ported content.
- **Supplementary surnames**: extend `SURNAMES` with the notable persons listed in [`docs/superpowers/specs/new-names.txt`](./new-names.txt), which is the source of truth for these additions — each entry keeps its original one-line bio + Wikipedia link comment, ported verbatim into `wordlists.py` in the same annotated-comment style as the Docker entries (do not flatten the annotations away when implementing).
  - Entries in that file already present in the ported Docker list are duplicates and must be **skipped**: `ramanujan`, `visvesvaraya`, `bhabha`, `feynman`, `torvalds`, `bardeen`, `burnell`, `moser`.
- `generator.py` exposes `generate_name(existing: set[str]) -> str`: picks a random `adjective_surname` combo; retries (capped, e.g. 20 attempts) on collision against `existing`; if still colliding after retries, appends an incrementing numeric suffix (Docker's own fallback behavior) until unique.
- `src/project_identity.py` (below) is the **only** consumer of `src/whimsy`, and only through the public `generate_name` function from `src/whimsy/__init__.py` — it must not reach into `wordlists.py` or `generator.py` directly, so the package's internal layout can change freely after extraction without breaking the caller.

### 4. Shared naming policy — `ProjectNameResolver` (`src/project_identity.py`)

The tri-state naming policy (mode branching, identity-store calls, error fallback) would otherwise be duplicated in both collectors. It lives once in a shared resolver:

```python
class ProjectNameResolver:
    def __init__(self, mode: str, identity_store: ProjectIdentityStore | None) -> None: ...

    def resolve(self, display_name: str | None, cwd: str | None) -> str | None:
        # "yes"       -> display_name (as provided by the collector)
        # "no"        -> identity_store.resolve_guid(cwd)
        # "whimsical" -> identity_store.resolve_whimsical(cwd)
        # missing cwd (for "no"/"whimsical") -> None
        # identity-store errors -> warn (once) and return None
```

- Single Responsibility: collectors parse their source files and supply the raw inputs; the resolver owns the naming policy end-to-end.
- The `identity_store` is required only when mode is `"no"`/`"whimsical"`; for `"yes"` it may be `None`.
- Identity-store errors (e.g. locked db file) are caught inside the resolver and logged as a warning (consistent with the existing `sqlite3.OperationalError` handling in `copilot_cli.py`), falling back to `None` rather than aborting collection.

### 5. Collector integration

- `CopilotCliCollector.__init__` / `ClaudeCliCollector.__init__`: replace `track_project_names: bool` with a single `resolver: ProjectNameResolver` dependency (constructor injection — collectors know nothing about modes or the identity store).
- Per record, each collector computes its source-specific inputs and delegates:
  - Copilot: `resolver.resolve(repo_name or cwd_basename, row["cwd"])` — repo name preferred for the `"yes"` display name, as today.
  - Claude: `resolver.resolve(cwd_basename, entry.get("cwd"))`.
- The **raw cwd** is always the identity key for `"no"`/`"whimsical"`; the repository name is only ever a display name for `"yes"`.

### 6. Wiring — `tracker.py`

- `_build_pipeline` instantiates one shared `ProjectIdentityStore(cfg.db_path)`, wraps it in a single `ProjectNameResolver(cfg.track_project_names, identity_store)`, and passes that resolver into both collectors.
- `cmd_config_set` validates `track_project_names` against `{"yes", "no", "whimsical"}` instead of boolean parsing.

## Testing

- `tests/test_project_identity.py`: guid stability across case variations of the same cwd; distinct cwds → distinct guids; whimsical name stability tied to guid; concurrent/duplicate resolution is idempotent.
- `tests/test_project_resolver.py`: mode branching for `"yes"`/`"no"`/`"whimsical"`; missing cwd → `None`; identity-store error → warning + `None`. The policy is tested once here — collector tests only verify that the right `display_name`/`cwd` inputs are passed to a stub resolver.
- `tests/whimsy/test_generator.py`: generated names match `adjective_surname` format; collision retry produces a unique name; numeric-suffix fallback after exhausting retries. Kept isolated from the rest of `tests/` (imports only `src.whimsy`) so it travels cleanly with the component on extraction.
- Update existing tests (`test_config.py`, `test_cli_collector.py`, `test_claude_cli_collector.py`, `test_config_stores.py`, `test_context.py`) for the new resolver-injection API.
- Regression test confirming `project_identities` rows are never included in `SqliteStore.unsynced_for(...)` output / never pushed to a remote store.

## Documentation updates

The following docs must be updated alongside the implementation, since they document the current boolean `track_project_names` behavior:

- **`README.md`**: update any `track_project_names` / `--track-projects` / `--no-track-projects` usage examples to the new `"yes"|"no"|"whimsical"` values and the `--project-mode` flag. Add a short explanation of the `"no"` (guid) and `"whimsical"` (masked name) modes and the local-only `project_identities` table.
- **`CLAUDE.md`**: update the `[tracking]` config description (currently documents `track_project_names` as a plain bool) and the `src/` architecture map to list the new `src/project_identity.py` module and the standalone `src/whimsy/` package, following the existing "Adding a new collector" style of documentation. Note that `src/whimsy/` is intentionally self-contained (no imports from the rest of `src/`) so it can be extracted into its own repo later.
- **`docs/ARCHITECTURE.md`**: update the data-flow / module description to include `ProjectIdentityStore` and the whimsical-name generator in the collection pipeline, and note that `project_identities` is local-only and excluded from sync.

## Migration / compatibility notes

- This is a breaking change to `~/.tokentracer.toml` and the `collect` CLI flags. Existing `track_project_names = true|false` entries will need to be rewritten as `"yes"|"no"` — no automatic migration is performed, per explicit user direction that booleans need not be accepted.
- Historical `sessions.project` values already collected are untouched; only new collection runs are affected.
