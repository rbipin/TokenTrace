"""Normalized activity record shared by every collector."""

from __future__ import annotations

from dataclasses import dataclass, replace

UNKNOWN_MODEL = "unknown"
NO_SCOPE = ""


def _min_ts(a: str | None, b: str | None) -> str | None:
    """Return the earlier of two ISO timestamps, ignoring ``None``."""
    candidates = [t for t in (a, b) if t]
    return min(candidates) if candidates else None


def _max_ts(a: str | None, b: str | None) -> str | None:
    """Return the later of two ISO timestamps, ignoring ``None``."""
    candidates = [t for t in (a, b) if t]
    return max(candidates) if candidates else None


@dataclass(frozen=True)
class ActivityRecord:
    """One day's Copilot activity for a single source/model/scope combination.

    The tuple ``(date, source, model, scope)`` is the storage grain and primary
    key. All higher-level aggregations (month, year, per-model) are pure
    read-time roll-ups over these records, so nothing is ever pre-aggregated
    destructively.
    """

    date: str  # YYYY-MM-DD (local)
    source: str  # e.g. "copilot-cli"
    model: str = UNKNOWN_MODEL
    scope: str = NO_SCOPE  # workspace folder or repository; "" when n/a
    sessions: int = 0
    prompts: int = 0
    turns: int = 0
    tool_calls: int = 0
    context_peak_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    first_ts: str | None = None
    last_ts: str | None = None

    @property
    def key(self) -> tuple[str, str, str, str]:
        """The storage grain / primary key for this record."""
        return (self.date, self.source, self.model, self.scope)

    def merge(self, other: "ActivityRecord") -> "ActivityRecord":
        """Combine two records that share the same key.

        Counts are summed; ``context_peak_tokens`` takes the maximum; timestamps
        widen to the earliest ``first_ts`` and latest ``last_ts``.
        """
        if self.key != other.key:
            raise ValueError(f"cannot merge records with different keys: {self.key} != {other.key}")
        return replace(
            self,
            sessions=self.sessions + other.sessions,
            prompts=self.prompts + other.prompts,
            turns=self.turns + other.turns,
            tool_calls=self.tool_calls + other.tool_calls,
            context_peak_tokens=max(self.context_peak_tokens, other.context_peak_tokens),
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            first_ts=_min_ts(self.first_ts, other.first_ts),
            last_ts=_max_ts(self.last_ts, other.last_ts),
        )


def merge_records(records: "list[ActivityRecord]") -> "list[ActivityRecord]":
    """Collapse a list of records, merging any that share the same key."""
    merged: dict[tuple[str, str, str, str], ActivityRecord] = {}
    for rec in records:
        existing = merged.get(rec.key)
        merged[rec.key] = existing.merge(rec) if existing else rec
    return list(merged.values())
