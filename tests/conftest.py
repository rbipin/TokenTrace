"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "usage.db"
