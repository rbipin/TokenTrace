"""Deprecated: use src.stores.sqlite.SqliteStore instead.

This module provides backward compatibility for code that imports UsageStore
from src.store. New code should import SqliteStore from src.stores.sqlite.
"""
from __future__ import annotations

import warnings

from .stores.sqlite import SqliteStore

# Deprecated alias for backward compatibility
UsageStore = SqliteStore

__all__ = ["UsageStore"]

# Warn on first import
warnings.warn(
    "src.store.UsageStore is deprecated; use src.stores.sqlite.SqliteStore instead",
    DeprecationWarning,
    stacklevel=2,
)
