# src/models.py
from __future__ import annotations

from dataclasses import dataclass

UNKNOWN_MODEL = "unknown"
DEFAULT_CONTEXT = "personal"


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    source: str
    model: str = UNKNOWN_MODEL
    date: str = ""
    start_ts: str | None = None
    end_ts: str | None = None
    project: str | None = None
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    context_peak_tokens: int = 0
    reasoning_tokens: int = 0
    context: str = DEFAULT_CONTEXT  # usage context label, e.g. "work" or "personal"
    canonical_model: str | None = None  # normalized model name, computed by ModelNormalizeMiddleware

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.session_id, self.source, self.model)


def merge_records(records: list[SessionRecord]) -> list[SessionRecord]:
    """Deduplicate by (session_id, source, model). Last writer wins."""
    merged: dict[tuple[str, str, str], SessionRecord] = {}
    for rec in records:
        merged[rec.key] = rec
    return list(merged.values())
