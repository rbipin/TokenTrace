"""Collector protocol and shared helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from ..models import ActivityRecord


@runtime_checkable
class ActivityCollector(Protocol):
    """A source of Copilot activity (one implementation per Copilot surface).

    Implementations must be side-effect free with respect to the data they read
    (read-only) and must only emit records dated on or after ``since``.
    """

    #: Stable identifier stored in the ``source`` column.
    source: str

    def collect(self, since: date) -> Iterable[ActivityRecord]:
        """Yield activity records for days on or after ``since``."""
        ...


def to_local_date(ts: datetime | str | int | float) -> str:
    """Normalize a timestamp to a local ``YYYY-MM-DD`` string.

    Accepts ``datetime``, ISO-8601 strings (``Z`` suffix allowed) and epoch
    values in seconds or milliseconds.
    """
    dt = _to_datetime(ts)
    return dt.astimezone().strftime("%Y-%m-%d")


def to_local_iso(ts: datetime | str | int | float) -> str:
    """Normalize a timestamp to a local ISO-8601 string (second precision)."""
    return _to_datetime(ts).astimezone().replace(microsecond=0).isoformat()


def _to_datetime(ts: datetime | str | int | float) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        # Heuristic: values that large are milliseconds since epoch.
        seconds = ts / 1000 if ts > 1e11 else ts
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(ts).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def file_touched_since(path: Path, since: date) -> bool:
    """True if ``path`` was modified on or after ``since`` (local time)."""
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return False
    return mtime >= since
