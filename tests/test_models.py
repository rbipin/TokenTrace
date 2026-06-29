"""Tests for ActivityRecord merge semantics."""

from __future__ import annotations

import pytest

from aitoken.models import ActivityRecord, merge_records


def test_merge_sums_counts_and_maxes_peak():
    a = ActivityRecord("2026-06-15", "vscode", "m", "s", sessions=1, prompts=2,
                        turns=2, tool_calls=1, context_peak_tokens=100,
                        first_ts="2026-06-15T09:00:00+00:00", last_ts="2026-06-15T10:00:00+00:00")
    b = ActivityRecord("2026-06-15", "vscode", "m", "s", sessions=1, prompts=3,
                        turns=3, tool_calls=4, context_peak_tokens=250,
                        first_ts="2026-06-15T08:00:00+00:00", last_ts="2026-06-15T11:00:00+00:00")
    merged = a.merge(b)
    assert merged.sessions == 2
    assert merged.prompts == 5
    assert merged.turns == 5
    assert merged.tool_calls == 5
    assert merged.context_peak_tokens == 250
    assert merged.first_ts == "2026-06-15T08:00:00+00:00"
    assert merged.last_ts == "2026-06-15T11:00:00+00:00"


def test_merge_rejects_different_keys():
    a = ActivityRecord("2026-06-15", "vscode", "m", "s")
    b = ActivityRecord("2026-06-15", "vscode", "m", "other")
    with pytest.raises(ValueError):
        a.merge(b)


def test_merge_records_collapses_by_key():
    records = [
        ActivityRecord("2026-06-15", "cli", "m", "", prompts=1),
        ActivityRecord("2026-06-15", "cli", "m", "", prompts=2),
        ActivityRecord("2026-06-15", "cli", "m", "x", prompts=5),
    ]
    out = {r.key: r for r in merge_records(records)}
    assert out[("2026-06-15", "cli", "m", "")].prompts == 3
    assert out[("2026-06-15", "cli", "m", "x")].prompts == 5
