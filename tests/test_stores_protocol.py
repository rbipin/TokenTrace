from __future__ import annotations
from datetime import date
from pathlib import Path
import pytest
from src.stores import SessionStore
from src.stores.sqlite import SqliteStore
from src.models import SessionRecord


class _MinimalStore:
    name = "minimal"

    def upsert(self, records: list[SessionRecord]) -> int:
        return len(records)

    def close(self) -> None:
        pass


def test_minimal_store_satisfies_protocol():
    store: SessionStore = _MinimalStore()  # type: ignore[assignment]
    assert store.name == "minimal"
    assert store.upsert([]) == 0
    store.close()


class TestSqliteStore:
    """Test SqliteStore implementation of SessionStore protocol."""

    def test_satisfies_protocol(self, tmp_db: Path):
        """SqliteStore must implement SessionStore protocol."""
        store: SessionStore = SqliteStore(tmp_db)  # type: ignore[assignment]
        assert store.name == "sqlite"
        assert store.upsert([]) == 0
        store.close()

    def test_upsert_empty_list(self, tmp_db: Path):
        """Upsert with empty list returns 0."""
        store = SqliteStore(tmp_db)
        result = store.upsert([])
        assert result == 0
        store.close()

    def test_upsert_single_record(self, tmp_db: Path):
        """Upsert single record returns 1."""
        store = SqliteStore(tmp_db)
        rec = SessionRecord(
            session_id="s1",
            source="test",
            model="claude-sonnet-4-6",
            date="2026-07-06",
            input_tokens=100,
            output_tokens=50,
        )
        result = store.upsert([rec])
        assert result == 1
        store.close()

    def test_upsert_multiple_records(self, tmp_db: Path):
        """Upsert multiple records returns count."""
        store = SqliteStore(tmp_db)
        records = [
            SessionRecord(
                session_id="s1",
                source="test",
                model="claude-sonnet-4-6",
                date="2026-07-06",
                input_tokens=100,
                output_tokens=50,
            ),
            SessionRecord(
                session_id="s2",
                source="test",
                model="claude-opus-4-8",
                date="2026-07-06",
                input_tokens=200,
                output_tokens=100,
            ),
        ]
        result = store.upsert(records)
        assert result == 2
        store.close()

    def test_upsert_replaces_existing(self, tmp_db: Path):
        """Upsert replaces existing records with same key."""
        store = SqliteStore(tmp_db)
        rec1 = SessionRecord(
            session_id="s1",
            source="test",
            model="claude-sonnet-4-6",
            date="2026-07-06",
            input_tokens=100,
            output_tokens=50,
        )
        rec2 = SessionRecord(
            session_id="s1",
            source="test",
            model="claude-sonnet-4-6",
            date="2026-07-06",
            input_tokens=150,
            output_tokens=75,
        )
        store.upsert([rec1])
        result = store.upsert([rec2])
        assert result == 1
        store.close()

    def test_creates_database(self, tmp_db: Path):
        """SqliteStore creates database file."""
        assert not tmp_db.exists()
        store = SqliteStore(tmp_db)
        store.close()
        assert tmp_db.exists()

    def test_creates_sessions_table(self, tmp_db: Path):
        """SqliteStore creates sessions table on init."""
        store = SqliteStore(tmp_db)
        rec = SessionRecord(
            session_id="s1",
            source="test",
            model="claude-sonnet-4-6",
            date="2026-07-06",
            input_tokens=100,
            output_tokens=50,
        )
        store.upsert([rec])
        store.close()
        # Verify database exists and is readable
        assert tmp_db.exists()

    def test_close_is_idempotent(self, tmp_db: Path):
        """Calling close multiple times is safe."""
        store = SqliteStore(tmp_db)
        store.close()
        store.close()  # Should not raise
