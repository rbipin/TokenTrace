"""Helpers shared by multiple commands."""
from __future__ import annotations

import sys

from src.config import Config
from src.stores.registry import instantiate_store


def load_remote_stores(cfg: Config) -> list:
    """Instantiate configured remote stores, warning on failures."""
    stores = []
    for sc in cfg.remote_stores:
        try:
            stores.append(instantiate_store(sc.name, sc.params, sc.class_path))
        except Exception as exc:
            print(f"Warning: could not load store {sc.name!r}: {exc}", file=sys.stderr)
    return stores


def run_sync(
    sqlite_store,
    remote_stores: list,
    dry_run: bool,
) -> dict:
    """Core sync logic — separated for testability.

    Returns a dict: {store_name: {"pushed": N, "failed": bool} | {"pending": N}}
    """
    result = {}
    for store in remote_stores:
        pending = sqlite_store.unsynced_for(store.name)
        if dry_run:
            result[store.name] = {"pending": len(pending)}
            store.close()
            continue
        try:
            if pending:
                store.upsert(pending)
                sqlite_store.mark_synced(pending, store.name)
            result[store.name] = {"pushed": len(pending), "failed": False}
        except Exception as exc:
            print(f"Warning [{store.name}]: {exc}", file=sys.stderr)
            result[store.name] = {"pushed": 0, "failed": True, "error": str(exc)}
        finally:
            try:
                store.close()
            except Exception:
                pass
    return result
