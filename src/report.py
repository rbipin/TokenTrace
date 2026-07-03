"""Read-time roll-ups over the daily grain (day / month / year, by model etc.)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .store import UsageStore

_PERIOD_EXPR = {
    "day": "date",
    "month": "substr(date, 1, 7)",
    "year": "substr(date, 1, 4)",
}


@dataclass(frozen=True)
class ReportRow:
    """One aggregated bucket of a usage report."""

    period: str
    source: str
    model: str
    sessions: int
    prompts: int
    turns: int
    tool_calls: int
    context_peak_tokens: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int


class UsageReporter:
    """Aggregates ``daily_activity`` by a chosen period and dimensions.

    Counts are summed; ``context_peak_tokens`` uses ``MAX`` because a peak is an
    instantaneous high-water mark, not something to add up.
    """

    def __init__(self, db_path: Path) -> None:
        self._store = UsageStore(db_path)

    def report(
        self,
        period: str = "day",
        sources: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
    ) -> list[ReportRow]:
        if period not in _PERIOD_EXPR:
            raise ValueError(f"unknown period: {period!r} (use day, month or year)")
        period_expr = _PERIOD_EXPR[period]

        where: list[str] = []
        params: list[str] = []
        if sources:
            where.append(f"source IN ({','.join('?' * len(sources))})")
            params.extend(sources)
        if models:
            where.append(f"model IN ({','.join('?' * len(models))})")
            params.extend(models)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
            SELECT {period_expr} AS period, source, model,
                   SUM(sessions)            AS sessions,
                   SUM(prompts)             AS prompts,
                   SUM(turns)               AS turns,
                   SUM(tool_calls)          AS tool_calls,
                   MAX(context_peak_tokens) AS context_peak_tokens,
                   SUM(input_tokens)        AS input_tokens,
                   SUM(output_tokens)       AS output_tokens,
                   SUM(cache_read_tokens)   AS cache_read_tokens,
                   SUM(cache_write_tokens)  AS cache_write_tokens,
                   SUM(reasoning_tokens)    AS reasoning_tokens
            FROM daily_activity
            {where_sql}
            GROUP BY period, source, model
            ORDER BY period DESC, source, model
        """
        conn = self._store.connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [
            ReportRow(
                period=r["period"], source=r["source"], model=r["model"],
                sessions=r["sessions"], prompts=r["prompts"], turns=r["turns"],
                tool_calls=r["tool_calls"], context_peak_tokens=r["context_peak_tokens"],
                input_tokens=r["input_tokens"] or 0,
                output_tokens=r["output_tokens"] or 0,
                cache_read_tokens=r["cache_read_tokens"] or 0,
                cache_write_tokens=r["cache_write_tokens"] or 0,
                reasoning_tokens=r["reasoning_tokens"] or 0,
            )
            for r in rows
        ]


def format_table(rows: Sequence[ReportRow]) -> str:
    """Render report rows as a fixed-width text table."""
    headers = [
        "PERIOD", "SOURCE", "MODEL", "SESSIONS", "PROMPTS", "TURNS", "TOOLS",
        "CTX_PEAK", "IN_TOK", "OUT_TOK", "CACHE_R", "CACHE_W", "REASON",
    ]
    data = [
        [
            r.period, r.source, r.model,
            str(r.sessions), str(r.prompts), str(r.turns), str(r.tool_calls),
            str(r.context_peak_tokens),
            str(r.input_tokens), str(r.output_tokens),
            str(r.cache_read_tokens), str(r.cache_write_tokens),
            str(r.reasoning_tokens),
        ]
        for r in rows
    ]
    widths = [len(h) for h in headers]
    for row in data:
        widths = [max(w, len(c)) for w, c in zip(widths, row)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [line, "  ".join("-" * w for w in widths)]
    out.extend("  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in data)
    return "\n".join(out)
