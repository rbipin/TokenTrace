"""Pluggable store interface for TokenTracer."""
from __future__ import annotations

from typing import Protocol

from ..models import SessionRecord


class SessionStore(Protocol):
    """Write-only sink for session records.

    Implementers must be safe to call from multiple threads (one call at a time).
    """

    name: str

    def upsert(self, records: list[SessionRecord]) -> int:
        """Persist records; return the count written."""
        ...

    def close(self) -> None:
        """Flush buffers and release resources."""
        ...
