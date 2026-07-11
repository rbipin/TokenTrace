"""Tests for TrackerPipeline multi-store support."""

from __future__ import annotations

from datetime import date

import pytest

from src.models import SessionRecord
from src.pipeline import TrackerPipeline
from src.stores.sqlite import SqliteStore


class _StubStore:
    def __init__(self, name, boom=False):
        self.name = name
        self._boom = boom
        self.received: list[SessionRecord] = []

    def upsert(self, records):
        if self._boom:
            raise RuntimeError("remote down")
        self.received.extend(records)
        return len(records)

    def close(self):
        pass


class _StubCollector:
    def __init__(self, source, records):
        self.source = source
        self._records = records

    def collect(self, since):
        return self._records


def _rec(sid):
    return SessionRecord(session_id=sid, source="claude_cli",
                         model="claude-sonnet-4-6", date="2026-07-01", turns=1)


def test_multi_store_all_succeed(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    remote = _StubStore("remote_a")
    col = _StubCollector("claude_cli", [_rec("s1")])
    result = (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, remote)
        .run()
    )
    assert result.records_written == 1
    assert result.stores_failed == []
    assert len(remote.received) == 1


def test_remote_failure_does_not_abort(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    bad_remote = _StubStore("remote_bad", boom=True)
    col = _StubCollector("claude_cli", [_rec("s1")])
    result = (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, bad_remote)
        .run()
    )
    assert result.records_written == 1          # sqlite succeeded
    assert len(result.stores_failed) == 1       # remote failure captured
    assert "remote_bad" in result.stores_failed[0]


def test_store_alias_still_works(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    col = _StubCollector("claude_cli", [_rec("s1")])
    result = (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .store(sqlite)          # deprecated alias
        .run()
    )
    assert result.records_written == 1


def test_successful_remote_push_marks_synced(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    remote = _StubStore("remote_a")
    col = _StubCollector("claude_cli", [_rec("s1")])
    (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, remote)
        .run()
    )
    assert sqlite.unsynced_for("remote_a") == []


def test_failed_remote_push_leaves_unsynced(tmp_path):
    sqlite = SqliteStore(tmp_path / "usage.db")
    bad_remote = _StubStore("remote_bad", boom=True)
    col = _StubCollector("claude_cli", [_rec("s1")])
    (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, bad_remote)
        .run()
    )
    assert len(sqlite.unsynced_for("remote_bad")) == 1


def test_mark_synced_failure_does_not_mark_push_as_failed(tmp_path, capsys):
    class FlakySqlite(SqliteStore):
        def mark_synced(self, records, store_name):
            raise RuntimeError("db locked")

    sqlite = FlakySqlite(tmp_path / "usage.db")
    remote = _StubStore("remote_a")
    col = _StubCollector("claude_cli", [_rec("s1")])
    result = (
        TrackerPipeline().add(col)
        .since(date(2026, 1, 1))
        .stores(sqlite, remote)
        .run()
    )
    assert result.stores_failed == []
    assert len(remote.received) == 1
    captured = capsys.readouterr()
    assert "remote_a" in captured.err
