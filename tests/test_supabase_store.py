"""Tests for SupabaseStore."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.models import SessionRecord
from src.stores.supabase import SupabaseStore


def _rec(
    sid: str = "s1",
    source: str = "claude",
    model: str = "claude-sonnet-4-6",
    canonical_model: str | None = "claude-sonnet-4-6",
) -> SessionRecord:
    return SessionRecord(
        session_id=sid,
        source=source,
        model=model,
        canonical_model=canonical_model,
        date="2026-07-01",
        start_ts="2026-07-01T10:00:00",
        end_ts="2026-07-01T11:00:00",
        project="myproject",
        turns=5,
        input_tokens=100,
        output_tokens=200,
        cache_creation_tokens=10,
        cache_read_tokens=20,
    )


def _make_store(url="https://x.supabase.co", key="service-role-secret", table="token_sessions"):
    return SupabaseStore(url=url, key=key, table=table)


def test_init_does_not_call_create_client():
    with patch("src.stores.supabase._create_client") as mock_create:
        _make_store()
        mock_create.assert_not_called()


def test_client_created_on_first_upsert():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client) as mock_create:
        store = _make_store(url="https://x.supabase.co", key="my-key")
        store.upsert([_rec()])
        mock_create.assert_called_once_with("https://x.supabase.co", "my-key")


def test_client_cached_across_upserts():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client) as mock_create:
        store = _make_store()
        store.upsert([_rec("s1")])
        store.upsert([_rec("s2")])
        mock_create.assert_called_once()


def test_upsert_sends_correct_row_shape():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        store.upsert([_rec()])

    rows = mock_client.table.return_value.upsert.call_args[0][0]
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "s1"
    assert row["source"] == "claude"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["date"] == "2026-07-01"
    assert row["start_ts"] == "2026-07-01T10:00:00"
    assert row["end_ts"] == "2026-07-01T11:00:00"
    assert row["project"] == "myproject"
    assert row["turns"] == 5
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 200
    assert row["cache_creation_tokens"] == 10
    assert row["cache_read_tokens"] == 20
    assert row["canonical_model"] == "claude-sonnet-4-6"


def test_upsert_includes_canonical_model_for_backfill():
    """collect --lookback + sync must re-push normalized names to remote stores."""
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        store.upsert([_rec(model="claude-sonnet-4-5-20250929", canonical_model="claude-sonnet-4-5")])

    rows = mock_client.table.return_value.upsert.call_args[0][0]
    assert rows[0]["model"] == "claude-sonnet-4-5-20250929"
    assert rows[0]["canonical_model"] == "claude-sonnet-4-5"


def test_upsert_uses_conflict_key():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        store.upsert([_rec()])

    kwargs = mock_client.table.return_value.upsert.call_args[1]
    assert kwargs.get("on_conflict") == "session_id,source,model"


def test_upsert_targets_correct_table():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store(table="custom_table")
        store.upsert([_rec()])

    mock_client.table.assert_called_with("custom_table")


def test_upsert_returns_record_count():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        result = store.upsert([_rec("s1"), _rec("s2")])

    assert result == 2


def test_upsert_empty_returns_zero_without_network_call():
    with patch("src.stores.supabase._create_client") as mock_create:
        store = _make_store()
        result = store.upsert([])
        assert result == 0
        mock_create.assert_not_called()


def test_close_clears_client_cache():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        store.upsert([_rec()])
        assert store._client_cache is not None
        store.close()
        assert store._client_cache is None


def test_name_attribute():
    store = _make_store()
    assert store.name == "supabase"


def test_upsert_includes_tool_and_token_detail_columns():
    mock_client = MagicMock()
    with patch("src.stores.supabase._create_client", return_value=mock_client):
        store = _make_store()
        rec = SessionRecord(
            session_id="s1", source="copilot_cli", model="m",
            date="2026-07-09", turns=3, tool_calls=7,
            input_tokens=100, output_tokens=50,
            reasoning_tokens=42, context_peak_tokens=2100,
        )
        store.upsert([rec])
    rows = mock_client.table.return_value.upsert.call_args[0][0]
    assert rows[0]["tool_calls"] == 7
    assert rows[0]["reasoning_tokens"] == 42
    assert rows[0]["context_peak_tokens"] == 2100
