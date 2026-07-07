"""Store discovery and registry via entry points."""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import SessionStore


class StoreNotFound(Exception):
    """Raised when a requested store is not found."""

    def __init__(self, name: str) -> None:
        """Initialize the exception with the store name."""
        super().__init__(f"Store not found: {name}")
        self.name = name


def discover_stores() -> dict[str, type[SessionStore]]:
    """Discover available stores via entry points.

    Returns a dict mapping store names to store classes. Includes built-in
    stores as a fallback if entry points are not found.

    Returns:
        Dict mapping store names (e.g., "sqlite") to store classes.
    """
    stores: dict[str, type[SessionStore]] = {}

    # Try to discover stores via entry points
    try:
        eps = importlib.metadata.entry_points()
        # Handle Python version differences:
        # - Python 3.10+ returns SelectableGroups (subscriptable)
        # - Python 3.9 returns a dict
        if hasattr(eps, "select"):
            # Python 3.10+
            store_eps = eps.select(group="tokentracer.stores")
        elif isinstance(eps, dict):
            # Python 3.9
            store_eps = eps.get("tokentracer.stores", [])
        else:
            # Fallback: treat as dict-like
            store_eps = eps.get("tokentracer.stores", [])

        for ep in store_eps:
            store_class = ep.load()
            if hasattr(store_class, "name"):
                stores[store_class.name] = store_class

    except Exception:
        # If entry point discovery fails, fall back to built-in stores
        pass

    # Fall back to built-in stores if none were discovered
    if not stores:
        from .sqlite import SqliteStore

        stores["sqlite"] = SqliteStore

    return stores


def get_store(name: str) -> type[SessionStore]:
    """Retrieve a store class by name.

    Supports both entry point discovery and a class= escape hatch for
    custom store implementations.

    Args:
        name: Store name (e.g., "sqlite") or class path (e.g.,
              "class=module.path:ClassName").

    Returns:
        The store class for the given name.

    Raises:
        StoreNotFound: If the store name is not found.
        ModuleNotFoundError: If a class= path references a non-existent module.
        AttributeError: If a class= path references a non-existent class.
    """
    # Handle class= escape hatch for custom stores
    if name.startswith("class="):
        path = name[6:]  # Remove "class=" prefix
        if ":" not in path:
            raise StoreNotFound(
                f"Invalid class path: {name} (expected format: class=module.path:ClassName)"
            )
        module_path, class_name = path.rsplit(":", 1)
        try:
            module = importlib.import_module(module_path)
            store_class = getattr(module, class_name)
            return store_class
        except ModuleNotFoundError as e:
            raise StoreNotFound(f"Cannot import {name}: {e}") from e
        except AttributeError as e:
            raise StoreNotFound(f"Cannot import {name}: {e}") from e

    # Discover stores and look up by name
    stores = discover_stores()
    if name not in stores:
        available = ", ".join(sorted(stores.keys()))
        raise StoreNotFound(
            f"Store '{name}' not found. Available: {available}"
        )
    return stores[name]
