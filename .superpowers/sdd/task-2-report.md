# Task 2 Report: Copilot Collector — Context Peak from assistant_usage_events

## Summary

Successfully implemented context peak token tracking for Copilot CLI sessions. The feature captures the maximum single-request footprint (input + output tokens) per session and model by querying the `assistant_usage_events` table.

## Implementation Details

### What Was Implemented

1. **Added `_query_context_peaks` staticmethod** — Queries `assistant_usage_events` table to calculate max footprint per (session_id, model) pair, filtering to main conversation only (agent_id IS NULL). Returns empty dict gracefully when table is missing (older CLI versions).

2. **Modified `collect()` method** — Now loads peaks from database before processing sessions and passes peaks through to _parse_events.

3. **Extended `_parse_events()` signature** — Added `peaks: dict[tuple[str, str], int]` parameter (after `project`), passes peaks to `_from_shutdown`, and uses peaks in fallback SessionRecord.

4. **Extended `_from_shutdown()` signature** — Added `peaks: dict[tuple[str, str], int]` parameter (after `tool_calls_by_model`), uses peaks in each yielded record.

5. **Updated fallback path** — When no shutdown event exists, SessionRecord gets context_peak_tokens from peaks dict (or 0 if missing).

### Test Results (TDD: RED → GREEN)

#### Step 2: Baseline Tests (RED)
```
FF.F                                                            [100%]
3 failed, 1 passed, 11 deselected in 0.64s
```

Failures:
- `test_context_peak_from_usage_events`: expected 2100, got 0
- `test_context_peak_excludes_subagent_rows`: expected 1050, got 0  
- `test_context_peak_in_fallback_path`: expected 730, got 0
- `test_context_peak_zero_when_table_missing`: PASS (trivial baseline)

#### Step 4: After Implementation (GREEN)
```
...............                                                 [100%]
15 passed in 1.21s
```

All tests pass:
- ✅ 4 new context_peak tests
- ✅ 11 existing tests (including Task 1 tool_calls tests)

### Files Changed

1. **src/collectors/copilot_cli.py**
   - Added `_query_context_peaks()` staticmethod (23 lines)
   - Modified `collect()` to load peaks (line 35-36: peaks = self._query_context_peaks(conn))
   - Modified `collect()` call to _parse_events to pass peaks (line 72)
   - Modified `_parse_events()` signature to accept peaks parameter
   - Modified `_parse_events()` call to _from_shutdown to pass peaks
   - Added context_peak_tokens to fallback SessionRecord (line 156)
   - Modified `_from_shutdown()` signature to accept peaks parameter
   - Added context_peak_tokens to each yielded SessionRecord in _from_shutdown (line 173)

2. **tests/test_cli_collector.py**
   - Added `_add_usage_events()` helper (12 lines)
   - Added `test_context_peak_from_usage_events()` (16 lines)
   - Added `test_context_peak_excludes_subagent_rows()` (14 lines)
   - Added `test_context_peak_zero_when_table_missing()` (11 lines)
   - Added `test_context_peak_in_fallback_path()` (12 lines)

### Commit

```
3837d21 feat: capture context peak tokens from Copilot assistant_usage_events
```

## Self-Review Findings

### Completeness vs Brief
✅ All requirements from task brief met:
- [x] Added `_query_context_peaks` staticmethod
- [x] Modified `_parse_events` with peaks parameter
- [x] Modified `_from_shutdown` with peaks parameter
- [x] Peaks passed through collect → _parse_events → _from_shutdown
- [x] Fallback path uses peaks
- [x] Handles missing table gracefully (returns {})
- [x] Filters to agent_id IS NULL (main conversation only)
- [x] All 4 test cases implemented and passing

### Test Coverage
✅ Tests verify real behavior:
- Test with multiple requests, captures peak (2100)
- Test filters subagent rows (agent_id="agent-123") correctly
- Test graceful handling when assistant_usage_events table missing
- Test fallback path (no shutdown event) still uses peaks (730)

### Code Quality
✅ No breaking changes:
- Task 1 tests (11 existing) all still pass
- Peak loading failure doesn't block session collection (try/except OperationalError)
- Peak query gracefully returns {} on missing table
- Preserves idempotency (last-write-wins on upsert key)

### Global Constraints Adherence
✅ All constraints maintained:
- No third-party runtime deps added
- No SQLite schema changes (context_peak_tokens column already exists)
- No backfill tooling added
- No writes to ~/.copilot (read-only collector)
- Idempotent: re-running `collect --lookback N` overwrites rows
- Conventional-commit message, no Co-authored-by trailer

## Concerns

None. Implementation is complete, well-tested, and adheres to all constraints.

## Testing Command

```bash
python -m pytest tests/test_cli_collector.py -q
```

Result: 15 passed ✓
