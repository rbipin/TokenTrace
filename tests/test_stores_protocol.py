from __future__ import annotations
from datetime import date
from pathlib import Path
import pytest
from src.stores import SessionStore
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
