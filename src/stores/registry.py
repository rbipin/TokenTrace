"""Store discovery and registry via entry points."""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import SessionStore

from src.config import _expand_env_vars


def load_store_registry() -> dict[str, type[SessionStore]]:
    """Discover available stores via entry points.

    Returns a dict mapping entry-point names to store classes. Falls back to
    the built-in SqliteStore if no entry points are found.

    Returns:
        Dict mapping store names (e.g., "sqlite") to store classes.
    """
    stores: dict[str, type[SessionStore]] = {}

    try:
        eps = importlib.metadata.entry_points()
        # Handle Python version differences:
        # - Python 3.10+ returns SelectableGroups (subscriptable)
        # - Python 3.9 returns a dict
        if hasattr(eps, "select"):
            store_eps = eps.select(group="tokentracer.stores")
        elif isinstance(eps, dict):
            store_eps = eps.get("tokentracer.stores", [])
        else:
            store_eps = eps.get("tokentracer.stores", [])

        for ep in store_eps:
            try:
                store_class = ep.load()
                stores[ep.name] = store_class
            except Exception as exc:
                print(
                    f"Warning: could not load store entry point {ep.name!r}: {exc}",
                    file=sys.stderr,
                )

    except Exception as exc:
        print(f"Warning: entry point discovery failed: {exc}", file=sys.stderr)

    # Fall back to built-in stores if none were discovered
    if not stores:
        from .sqlite import SqliteStore

        stores["sqlite"] = SqliteStore

    return stores


def instantiate_store(
    name: str,
    params: dict,
    class_path: str | None = None,
) -> "SessionStore":
    """Instantiate a store by name or class path.

    Args:
        name: Store name used for registry lookup (e.g., "sqlite").
        params: Constructor keyword arguments passed to the store class.
            ${VAR} placeholders in string values are expanded from environment variables.
        class_path: Optional dotted import path to a store class
            (e.g., "src.stores.sqlite.SqliteStore"). If provided, the registry
            is bypassed and this class is loaded directly.

    Returns:
        An instantiated SessionStore.

    Raises:
        ValueError: If the store name is not found and no class_path is given,
            or if a ${VAR} placeholder references a missing environment variable.
        ModuleNotFoundError: If class_path references a non-existent module.
        AttributeError: If class_path references a non-existent class.
    """
    # Expand environment variables in params
    expanded_params = _expand_env_vars(params)

    if class_path is not None:
        module_path, _, class_name = class_path.rpartition(".")
        module = importlib.import_module(module_path)
        store_class = getattr(module, class_name)
        return store_class(**expanded_params)

    registry = load_store_registry()
    if name not in registry:
        raise ValueError(
            f"Unknown store: {name!r}. Install a package providing a "
            "'tokentracer.stores' entry point for it, or use "
            "class_path='module.ClassName' in your config."
        )
    return registry[name](**expanded_params)
