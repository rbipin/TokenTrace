"""Supabase remote store for TokenTracer."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import SessionRecord

try:
    from supabase import create_client as _create_client
except ImportError:
    _create_client = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from supabase import Client


class SupabaseStore:
    """Remote store that upserts SessionRecords into a Supabase table."""

    name = "supabase"

    def __init__(self, url: str, key: str, table: str = "token_sessions") -> None:
        self._url = url
        self._key = key
        self._table = table
        self._client_cache: Client | None = None

    @property
    def _client(self) -> "Client":
        if self._client_cache is None:
            if _create_client is None:
                raise ImportError(
                    "supabase-py is required for SupabaseStore. "
                    "Install with: pip install tokentracer[supabase]"
                )
            self._client_cache = _create_client(self._url, self._key)
        return self._client_cache

    def upsert(self, records: list[SessionRecord]) -> int:
        """Upsert records into Supabase; returns the count submitted."""
        if not records:
            return 0
        rows = [
            {
                "session_id": r.session_id,
                "source": r.source,
                "model": r.model,
                "canonical_model": r.canonical_model,
                "date": r.date,
                "start_ts": r.start_ts,
                "end_ts": r.end_ts,
                "project": r.project,
                "turns": r.turns,
                "tool_calls": r.tool_calls,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_creation_tokens": r.cache_creation_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "context_peak_tokens": r.context_peak_tokens,
                "reasoning_tokens": r.reasoning_tokens,
                "context": r.context,
            }
            for r in records
        ]
        self._client.table(self._table).upsert(
            rows, on_conflict="session_id,source,model"
        ).execute()
        return len(records)

    def close(self) -> None:
        self._client_cache = None
