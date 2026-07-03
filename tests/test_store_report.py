"""Tests for UsageStore and UsageReporter (storage + roll-ups)."""

from __future__ import annotations

from src.models import ActivityRecord
from src.report import UsageReporter
from src.store import UsageStore


def _seed(db):
    store = UsageStore(db)
    store.upsert([
        ActivityRecord("2026-06-15", "vscode", "claude-sonnet-4", "wsA",
                       sessions=1, prompts=3, turns=3, context_peak_tokens=120),
        ActivityRecord("2026-06-16", "vscode", "claude-sonnet-4", "wsA",
                       sessions=1, prompts=2, turns=2, context_peak_tokens=200),
        ActivityRecord("2026-05-30", "copilot-cli", "unknown", "",
                       sessions=2, prompts=4, turns=4, context_peak_tokens=95000),
    ])
    return store


def test_upsert_is_idempotent(tmp_db):
    store = UsageStore(tmp_db)
    rec = ActivityRecord("2026-06-15", "vscode", "m", "s", prompts=3)
    store.upsert([rec])
    store.upsert([rec])  # same key again -> overwrite, not add
    conn = store.connect()
    try:
        row = conn.execute("SELECT prompts FROM daily_activity").fetchone()
    finally:
        conn.close()
    assert row["prompts"] == 3


def test_monthly_rollup_sums_and_maxes(tmp_db):
    _seed(tmp_db)
    rows = UsageReporter(tmp_db).report(period="month", sources=["vscode"])
    assert len(rows) == 1
    row = rows[0]
    assert row.period == "2026-06"
    assert row.prompts == 5            # 3 + 2 summed
    assert row.context_peak_tokens == 200  # max, not sum


def test_yearly_rollup_groups_all_months(tmp_db):
    _seed(tmp_db)
    rows = UsageReporter(tmp_db).report(period="year")
    by_key = {(r.period, r.source): r for r in rows}
    assert by_key[("2026", "vscode")].prompts == 5
    assert by_key[("2026", "copilot-cli")].context_peak_tokens == 95000


def test_model_filter(tmp_db):
    _seed(tmp_db)
    rows = UsageReporter(tmp_db).report(period="year", models=["unknown"])
    assert all(r.model == "unknown" for r in rows)
    assert len(rows) == 1
