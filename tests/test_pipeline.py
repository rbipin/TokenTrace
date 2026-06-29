"""Tests for the fluent TrackerPipeline."""

from __future__ import annotations

from datetime import date

import pytest

from aitoken.models import ActivityRecord
from aitoken.pipeline import TrackerPipeline
from aitoken.report import UsageReporter
from aitoken.store import UsageStore


class _StubCollector:
    def __init__(self, source, records, boom=False):
        self.source = source
        self._records = records
        self._boom = boom

    def collect(self, since: date):
        if self._boom:
            raise RuntimeError("kaboom")
        return self._records


def test_pipeline_merges_and_writes(tmp_db):
    c1 = _StubCollector("copilot-cli", [ActivityRecord("2026-06-15", "copilot-cli", "m", "s", prompts=2)])
    c2 = _StubCollector("copilot-cli", [ActivityRecord("2026-06-15", "copilot-cli", "m", "s", prompts=3)])
    result = (
        TrackerPipeline().add(c1).add(c2)
        .since(date(2026, 1, 1)).store(UsageStore(tmp_db)).run()
    )
    assert result.records_written == 1  # merged to one row
    rows = UsageReporter(tmp_db).report(period="day")
    assert rows[0].prompts == 5


def test_pipeline_isolates_failing_collector(tmp_db):
    good = _StubCollector("copilot-cli", [ActivityRecord("2026-06-15", "copilot-cli", "m", "s", prompts=1)])
    bad = _StubCollector("copilot-cli", [], boom=True)
    result = TrackerPipeline().add(bad).add(good).since(date(2026, 1, 1)).store(UsageStore(tmp_db)).run()
    assert result.records_written == 1
    assert any("copilot-cli" in e for e in result.errors)


def test_pipeline_requires_since_and_store(tmp_db):
    with pytest.raises(ValueError):
        TrackerPipeline().store(UsageStore(tmp_db)).run()
    with pytest.raises(ValueError):
        TrackerPipeline().since(date(2026, 1, 1)).run()
