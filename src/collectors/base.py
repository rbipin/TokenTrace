# src/collectors/base.py
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterator, Protocol

from ..models import SessionRecord


class ActivityCollector(Protocol):
    """Read-only source that yields session records."""

    source: str

    def collect(self, since: date) -> Iterator[SessionRecord]:
        ...


# ── timestamp helpers ────────────────────────────────────────────────────────

def _parse_ts(ts: object) -> datetime | None:
    """Parse a timestamp that may be ISO-8601 string or epoch ms/s float."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        seconds = ts / 1000 if ts > 1e11 else ts
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def to_date(ts: object) -> date | None:
    """Convert any timestamp form to a local date, or None on failure."""
    dt = _parse_ts(ts)
    return dt.astimezone().date() if dt else None


def to_local_iso(ts: object) -> str | None:
    """Convert any timestamp form to a local ISO-8601 string, or None."""
    dt = _parse_ts(ts)
    return dt.astimezone().replace(microsecond=0).isoformat() if dt else None
