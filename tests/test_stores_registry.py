"""Tests for the stores registry and discovery."""

from __future__ import annotations

import pytest
from src.stores.registry import discover_stores, get_store, StoreNotFound
from src.stores.sqlite import SqliteStore
from src.stores import SessionStore


class TestDiscoverStores:
    """Test store discovery via entry points."""

    def test_discover_stores_returns_dict(self):
        """discover_stores returns a dict mapping store names to store classes."""
        stores = discover_stores()
        assert isinstance(stores, dict)

    def test_discover_stores_includes_sqlite(self):
        """discover_stores includes the built-in sqlite store."""
        stores = discover_stores()
        assert "sqlite" in stores
        assert stores["sqlite"] is SqliteStore

    def test_discover_stores_sqlite_is_session_store(self):
        """The discovered sqlite store must implement SessionStore protocol."""
        stores = discover_stores()
        store_class = stores["sqlite"]
        # Verify it's the right class
        assert store_class is SqliteStore
        # Verify it has the required attributes/methods
        assert hasattr(store_class, "name")
        assert hasattr(store_class, "upsert")
        assert hasattr(store_class, "close")


class TestGetStore:
    """Test store retrieval by name."""

    def test_get_store_by_name(self):
        """get_store retrieves a store class by name."""
        store_class = get_store("sqlite")
        assert store_class is SqliteStore

    def test_get_store_unknown_name_raises(self):
        """get_store raises StoreNotFound for unknown store names."""
        with pytest.raises(StoreNotFound) as exc:
            get_store("nonexistent")
        assert "nonexistent" in str(exc.value)

    def test_get_store_class_escape_hatch(self):
        """get_store supports class= escape hatch for custom stores."""
        # Using the built-in SqliteStore as a test case
        store_class = get_store("class=src.stores.sqlite:SqliteStore")
        assert store_class is SqliteStore

    def test_get_store_class_escape_hatch_unknown_module(self):
        """get_store class= raises for unknown modules."""
        with pytest.raises((ModuleNotFoundError, StoreNotFound)):
            get_store("class=nonexistent.module:SomeStore")

    def test_get_store_class_escape_hatch_unknown_class(self):
        """get_store class= raises for unknown classes in known modules."""
        with pytest.raises((AttributeError, StoreNotFound)):
            get_store("class=src.stores.sqlite:UnknownStore")


class TestStoreNotFound:
    """Test StoreNotFound exception."""

    def test_store_not_found_is_exception(self):
        """StoreNotFound is an Exception."""
        exc = StoreNotFound("test")
        assert isinstance(exc, Exception)

    def test_store_not_found_message(self):
        """StoreNotFound includes the store name in the message."""
        exc = StoreNotFound("mystore")
        assert "mystore" in str(exc)
