"""Tests for the fluent TrackerPipeline."""

from __future__ import annotations

from datetime import date

import pytest

from src.models import SessionRecord
from src.pipeline import TrackerPipeline
from src.report import UsageReporter
from src.store import UsageStore


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
    rec = SessionRecord(session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
                        date="2026-06-15", turns=3, input_tokens=100)
    # Same session yielded by two collectors — last writer wins → 1 row
    c1 = _StubCollector("claude_cli", [rec])
    c2 = _StubCollector("claude_cli", [rec])
    result = (
        TrackerPipeline().add(c1).add(c2)
        .since(date(2026, 1, 1)).store(UsageStore(tmp_db)).run()
    )
    assert result.records_written == 1
    output = UsageReporter(tmp_db).report(period="day")
    assert isinstance(output, str)


def test_pipeline_isolates_failing_collector(tmp_db):
    rec = SessionRecord(session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
                        date="2026-06-15", turns=1, input_tokens=50)
    good = _StubCollector("claude_cli", [rec])
    bad = _StubCollector("claude_cli", [], boom=True)
    result = TrackerPipeline().add(bad).add(good).since(date(2026, 1, 1)).store(UsageStore(tmp_db)).run()
    assert result.records_written == 1
    assert any("claude_cli" in e for e in result.errors)


def test_pipeline_requires_since_and_store(tmp_db):
    with pytest.raises(ValueError):
        TrackerPipeline().store(UsageStore(tmp_db)).run()
    with pytest.raises(ValueError):
        TrackerPipeline().since(date(2026, 1, 1)).run()
