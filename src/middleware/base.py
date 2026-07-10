"""Pipes-and-Filters middleware protocol for TrackerPipeline."""
from __future__ import annotations

from typing import Protocol

from ..models import SessionRecord


class RecordMiddleware(Protocol):
    """A pluggable batch transform stage between collection and persistence.

    Every applicable middleware transforms the batch and always forwards it
    to the next stage (Pipes-and-Filters) — nothing here short-circuits
    the chain the way Chain of Responsibility would.
    """

    name: str

    def applies(self, records: list[SessionRecord]) -> bool:
        """Return True if this middleware should run for this batch."""
        ...

    def process(self, records: list[SessionRecord]) -> list[SessionRecord]:
        """Transform the batch and return the new batch."""
        ...
