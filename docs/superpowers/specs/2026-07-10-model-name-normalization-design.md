# Model Name Normalization

## Problem

Raw model identifiers stored in `sessions.model` fragment reporting in two ways:

1. **Within-source drift.** Claude CLI reports both alias-form names (`claude-sonnet-4-6`) and dated snapshot names (`claude-haiku-4-5-20251001`) for what is effectively the same model, depending on when/how the session was recorded. `report.py`'s `GROUP BY ... model` treats these as distinct rows, fragmenting usage stats.
2. **Cross-harness drift.** Different harnesses (Copilot CLI, Claude CLI, and future ones) may report the same underlying model under entirely different, vendor-specific strings, with no structural relationship between them (e.g. Copilot's own SKU name for a Claude model vs. Anthropic's own model ID).

Goal: normalize both cases into a stable `canonical_model` for reporting, without losing the raw value, and support backfilling historical rows.

## Design

### Normalization function — `src/model_normalize.py`

```python
def normalize_model(raw: str, source: str) -> str
```

Applied in order:

1. **Regex.** Strip a trailing `-YYYYMMDD` date suffix: pattern `^(.+)-(\d{8})$`. Source-agnostic — the pattern is specific enough that it won't false-positive on non-dated names. Handles Anthropic's snapshot naming convention generically, with no maintenance burden as new dated snapshots ship.
2. **Lookup.** If the regex didn't match, look up `(source, raw)` in a static table for cases the regex structurally cannot resolve (arbitrary cross-vendor naming).
3. **Passthrough.** If neither matches, `canonical_model = raw`, unchanged. This also correctly handles sentinel values (`unknown`, `<synthetic>`), which will never match steps 1 or 2.

### Lookup table — `src/model_aliases.toml`

Bundled with the package (not the user's `~/.tokentracer.toml`, which holds user-specific config, not static reference data):

```toml
[copilot_cli]
"claude-sonnet-4.5" = "claude-sonnet-4-5"
```

Keyed by source, then raw string, to canonical name. Maintained manually as new harnesses/models are added — this is unavoidable for arbitrary cross-vendor name reconciliation.

### Schema change

Add `canonical_model TEXT` column to `sessions` (migration in `SqliteStore` init, since existing DBs need the column added). Computed once per record: collectors call `normalize_model(raw_model, source)` after extracting the raw `model` field, and store both.

- `model` — raw, exactly as reported by the source harness. Preserved so normalization logic can be corrected/re-applied later without re-collecting from source logs.
- `canonical_model` — normalized. Used for grouping/filtering in reports.

`report.py` switches its `GROUP BY` / filter columns from `model` to `canonical_model`. The existing dedup primary key `(session_id, source, model)` is unaffected — it continues to key off the raw value.

### Sync-log fix (general)

Today, `SqliteStore.upsert()` is a plain `INSERT OR REPLACE` that never touches `sync_log`, and `unsynced_for()` only checks whether a `sync_log` row *exists* for a given PK — it doesn't compare values. This means re-running `collect` over an already-synced window silently refreshes local values but never re-pushes them to remote, for *any* field, not just `canonical_model`. Verified in `src/stores/sqlite.py`.

Fix: `upsert()` now does read-before-write. Before `INSERT OR REPLACE`, `SELECT` the existing row for `(session_id, source, model)`, diff it against the incoming record, and if anything differs, `DELETE FROM sync_log WHERE session_id=? AND source=? AND model=?` before writing. This makes `unsynced_for()` pick the row up again on the next `sync` run — fixing the general staleness gap, of which model-normalization backfill is one instance.

### Backfill workflow

No new command needed. Existing two-step flow already covers it:

1. `tokentracer collect --lookback 90` — re-parses source logs for the window, recomputes `canonical_model` via `normalize_model`, re-upserts. Rows whose `canonical_model` changes get their `sync_log` entries cleared by the fix above.
2. `tokentracer sync` — pushes now-unsynced rows (including backfilled ones) to remote stores.

## Out of scope

- A `reconcile-models`-style standalone command — the existing `collect --lookback` + `sync` flow covers backfill.
- General data-quality auditing beyond the sync-log staleness fix described above.
