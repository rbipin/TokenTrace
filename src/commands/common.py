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
