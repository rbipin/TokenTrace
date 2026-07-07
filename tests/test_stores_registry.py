"""Tests for the stores registry and discovery."""

from __future__ import annotations

import pytest
from src.stores.registry import load_store_registry, instantiate_store
from src.stores.sqlite import SqliteStore
from src.stores.supabase import SupabaseStore


def test_sqlite_in_registry():
    registry = load_store_registry()
    assert "sqlite" in registry
    assert registry["sqlite"] is SqliteStore


def test_instantiate_by_name(tmp_path):
    store = instantiate_store("sqlite", {"db_path": tmp_path / "usage.db"})
    assert store.name == "sqlite"
    store.close()


def test_instantiate_by_class_path(tmp_path):
    store = instantiate_store(
        "sqlite",
        {"db_path": tmp_path / "usage.db"},
        class_path="src.stores.sqlite.SqliteStore",
    )
    assert store.name == "sqlite"
    store.close()


def test_unknown_store_raises():
    with pytest.raises(ValueError, match="Unknown store"):
        instantiate_store("nonexistent", {})


def test_load_store_registry_returns_dict():
    """load_store_registry returns a dict mapping store names to store classes."""
    registry = load_store_registry()
    assert isinstance(registry, dict)


def test_sqlite_store_has_required_interface():
    """The discovered sqlite store must implement SessionStore protocol."""
    registry = load_store_registry()
    store_class = registry["sqlite"]
    assert store_class is SqliteStore
    assert hasattr(store_class, "name")
    assert hasattr(store_class, "upsert")
    assert hasattr(store_class, "close")


def test_supabase_in_registry():
    registry = load_store_registry()
    assert "supabase" in registry
    assert registry["supabase"] is SupabaseStore


def test_supabase_store_has_required_interface():
    """The discovered supabase store must implement SessionStore protocol."""
    registry = load_store_registry()
    store_class = registry["supabase"]
    assert store_class is SupabaseStore
    assert hasattr(store_class, "name")
    assert hasattr(store_class, "upsert")
    assert hasattr(store_class, "close")


def test_supabase_store_instantiates_via_class_path(tmp_path):
    from src.stores.registry import instantiate_store
    from src.stores.supabase import SupabaseStore

    store = instantiate_store(
        "supabase",
        {"url": "https://x.supabase.co", "key": "secret"},
        class_path="src.stores.supabase.SupabaseStore",
    )
    assert isinstance(store, SupabaseStore)
    assert store._url == "https://x.supabase.co"
    assert store._key == "secret"
    assert store._table == "token_sessions"
    store.close()
