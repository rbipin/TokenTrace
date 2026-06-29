"""Configuration: filesystem locations and run parameters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def default_db_path() -> Path:
    """Where the tracker stores its own SQLite database (next to tracker.py)."""
    return Path(__file__).resolve().parents[1] / "usage.db"


@dataclass(frozen=True)
class Paths:
    """Resolved on-disk locations the collectors read from.

    Every path is overridable, which is what makes the collectors testable
    against fixture directories.
    """

    copilot_home: Path = field(default_factory=lambda: Path.home() / ".copilot")


@dataclass(frozen=True)
class Config:
    """Top-level run configuration."""

    paths: Paths = field(default_factory=Paths)
    db_path: Path = field(default_factory=default_db_path)
    lookback_days: int = 3
