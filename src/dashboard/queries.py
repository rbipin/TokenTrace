from __future__ import annotations

import sqlite3

_TOKENS_EXPR = (
    "input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens"
)

_DATE_RANGE_SQL = {
    "all":   "1=1",
    "day":   "date = date('now', 'localtime')",
    "week":  "date >= date('now', '-6 days', 'localtime')",
    "month": "date >= date('now', 'start of month', 'localtime')",
    "year":  "date >= date('now', 'start of year', 'localtime')",
}


def date_filter(period: str, start: str | None, end: str | None) -> tuple[str, list]:
    """Return a WHERE fragment (no leading AND) and its bind params."""
    if period == "custom":
        if not start or not end:
            raise ValueError("period=custom requires both start and end")
        return "date BETWEEN ? AND ?", [start, end]
    if period not in _DATE_RANGE_SQL:
        raise ValueError(f"period must be one of {list(_DATE_RANGE_SQL) + ['custom']}")
    return _DATE_RANGE_SQL[period], []


def summary(
    conn: sqlite3.Connection,
    period: str,
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    source: str | None = None,
) -> dict:
    where, params = date_filter(period, start, end)
    extra = ""
    if project:
        extra += " AND project = ?"
        params.append(project)
    if source:
        extra += " AND source = ?"
        params.append(source)

    totals = conn.execute(f"""
        SELECT
            COALESCE(SUM(input_tokens), 0)           AS input_tokens,
            COALESCE(SUM(output_tokens), 0)          AS output_tokens,
            COALESCE(SUM(cache_read_tokens), 0)      AS cache_read_tokens,
            COALESCE(SUM(cache_creation_tokens), 0)  AS cache_creation_tokens,
            COALESCE(SUM(reasoning_tokens), 0)       AS reasoning_tokens,
            COUNT(*)                                 AS session_count,
            COUNT(DISTINCT date)                     AS active_days,
            MIN(date)                                AS first_date
        FROM sessions
        WHERE {where}{extra}
    """, params).fetchone()

    total_tokens = (
        totals["input_tokens"] + totals["output_tokens"]
        + totals["cache_read_tokens"] + totals["cache_creation_tokens"]
    )

    harness_rows = conn.execute(f"""
        SELECT source,
               SUM({_TOKENS_EXPR}) AS tokens,
               COUNT(DISTINCT COALESCE(canonical_model, model)) AS model_count
        FROM sessions
        WHERE {where}{extra}
        GROUP BY source
        ORDER BY tokens DESC
    """, params).fetchall()

    model_rows = conn.execute(f"""
        SELECT COALESCE(canonical_model, model) AS model,
               SUM({_TOKENS_EXPR}) AS tokens
        FROM sessions
        WHERE {where}{extra}
        GROUP BY COALESCE(canonical_model, model)
        ORDER BY tokens DESC
    """, params).fetchall()

    def pct(tokens: int) -> float:
        return (tokens / total_tokens) if total_tokens else 0.0

    return {
        "total_tokens": total_tokens,
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "cache_read_tokens": totals["cache_read_tokens"],
        "cache_creation_tokens": totals["cache_creation_tokens"],
        "reasoning_tokens": totals["reasoning_tokens"],
        "session_count": totals["session_count"],
        "active_days": totals["active_days"],
        "first_date": totals["first_date"],
        "harnesses": [
            {"source": r["source"], "tokens": r["tokens"],
             "model_count": r["model_count"], "pct": pct(r["tokens"])}
            for r in harness_rows
        ],
        "models": [
            {"model": r["model"], "tokens": r["tokens"], "pct": pct(r["tokens"])}
            for r in model_rows
        ],
    }


def heatmap(conn: sqlite3.Connection, days: int = 180) -> list[dict]:
    rows = conn.execute(f"""
        SELECT date, SUM({_TOKENS_EXPR}) AS tokens
        FROM sessions
        WHERE date >= date('now', ?, 'localtime')
        GROUP BY date
        ORDER BY date
    """, [f"-{days - 1} days"]).fetchall()
    return [{"date": r["date"], "tokens": r["tokens"]} for r in rows]


def trend(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    rows = conn.execute(f"""
        SELECT date, source, SUM({_TOKENS_EXPR}) AS tokens
        FROM sessions
        WHERE date >= date('now', ?, 'localtime')
        GROUP BY date, source
        ORDER BY date, source
    """, [f"-{days - 1} days"]).fetchall()
    return [{"date": r["date"], "source": r["source"], "tokens": r["tokens"]} for r in rows]
