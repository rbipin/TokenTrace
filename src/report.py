from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

_PERIOD_SQL = {
    "all":   "strftime('%Y-%m', date)",
    "day":   "date",
    "month": "strftime('%Y-%m', date)",
    "year":  "strftime('%Y', date)",
}

_DATE_RANGE_SQL = {
    "all":   "1=1",
    "day":   "date = date('now', 'localtime')",
    "month": "date >= date('now', 'start of month', 'localtime')",
    "year":  "date >= date('now', 'start of year', 'localtime')",
}


@dataclass
class ReportContext:
    """All data a render strategy needs — replaces the 7-arg positional clump."""

    conn: sqlite3.Connection
    period: str
    date_filter: str
    model_filter: str
    params: list
    hit_rate: float
    cost_saved: float
    as_json: bool


class ReportStrategy(Protocol):
    def render(self, ctx: ReportContext) -> str: ...


# ── Concrete strategy classes ──────────────────────────────────────────────


class SessionsDetailedView:
    """Default view: one row per session, full token breakdown."""

    def render(self, ctx: ReportContext) -> str:
        rows = ctx.conn.execute(f"""
            SELECT
                COALESCE(project, '—')                              AS project,
                source,
                model,
                start_ts,
                end_ts,
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cache_read_tokens,
                cache_creation_tokens,
                context_peak_tokens,
                turns,
                tool_calls,
                input_tokens + cache_creation_tokens + cache_read_tokens AS denom
            FROM sessions
            WHERE {ctx.date_filter}{ctx.model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, ctx.params).fetchall()

        if ctx.as_json:
            return json.dumps({
                "cache_efficiency": {
                    "hit_rate": round(ctx.hit_rate, 4),
                    "cost_saved_rate": round(ctx.cost_saved, 4),
                },
                "rows": [dict(r) for r in rows],
            }, indent=2)

        table_rows = []
        for r in rows:
            denom = r["denom"] or 0
            cache_pct = f"{r['cache_read_tokens'] / denom:.0%}" if denom else "—"
            table_rows.append([
                r["project"],
                r["source"],
                r["model"],
                (r["start_ts"] or "")[:19],
                (r["end_ts"] or "")[:19],
                r["input_tokens"],
                r["output_tokens"],
                r["reasoning_tokens"],
                r["cache_read_tokens"],
                r["cache_creation_tokens"],
                cache_pct,
                r["context_peak_tokens"],
                r["turns"],
                r["tool_calls"],
            ])

        return _format_table(
            ctx.hit_rate,
            ctx.cost_saved,
            headers=["Project", "Source", "Model", "Start", "End",
                     "Input", "Output", "Reasoning", "CacheRead", "CacheCreate",
                     "CacheHit%", "CtxPeak", "Turns", "Tools"],
            rows=table_rows,
        )


class PeriodSummaryView:
    """--summary --period month/year/all: aggregated roll-up grouped by period + model."""

    def render(self, ctx: ReportContext) -> str:
        period_expr = _PERIOD_SQL[ctx.period]
        rows = ctx.conn.execute(f"""
            SELECT
                {period_expr}                       AS period,
                source,
                model,
                SUM(turns)                          AS turns,
                SUM(input_tokens)                   AS input_tokens,
                SUM(output_tokens)                  AS output_tokens,
                SUM(cache_creation_tokens)          AS cache_creation_tokens,
                SUM(cache_read_tokens)              AS cache_read_tokens
            FROM sessions
            WHERE {ctx.date_filter}{ctx.model_filter}
            GROUP BY {period_expr}, source, model
            ORDER BY period DESC, input_tokens DESC
        """, ctx.params).fetchall()

        if ctx.as_json:
            return json.dumps({
                "cache_efficiency": {
                    "hit_rate": round(ctx.hit_rate, 4),
                    "cost_saved_rate": round(ctx.cost_saved, 4),
                },
                "rows": [dict(r) for r in rows],
            }, indent=2)

        return _format_table(
            ctx.hit_rate,
            ctx.cost_saved,
            headers=["Period", "Source", "Model", "Turns",
                     "Input", "Output", "CacheCreate", "CacheRead"],
            rows=[
                [r["period"], r["source"], r["model"], r["turns"],
                 r["input_tokens"], r["output_tokens"],
                 r["cache_creation_tokens"], r["cache_read_tokens"]]
                for r in rows
            ],
        )


class ByProjectView:
    """--by-project: group sessions by project name."""

    def render(self, ctx: ReportContext) -> str:
        rows = ctx.conn.execute(f"""
            SELECT
                project,
                date,
                model,
                SUM(turns)              AS turns,
                SUM(input_tokens)       AS input_tokens,
                SUM(output_tokens)      AS output_tokens,
                SUM(cache_read_tokens)  AS cache_read_tokens
            FROM sessions
            WHERE project IS NOT NULL
              AND {ctx.date_filter}{ctx.model_filter}
            GROUP BY project, date, model
            ORDER BY SUM(input_tokens + cache_read_tokens) DESC
        """, ctx.params).fetchall()

        if not rows:
            note = (
                "No project data found. Enable project tracking:\n"
                "  python3 tracker.py config set track_project_names yes\n"
                "Then re-collect: python3 tracker.py collect --project-mode yes"
            )
            if ctx.as_json:
                return json.dumps({"note": note, "rows": []}, indent=2)
            return note

        if ctx.as_json:
            return json.dumps({
                "cache_efficiency": {"hit_rate": round(ctx.hit_rate, 4)},
                "rows": [dict(r) for r in rows],
            }, indent=2)

        return _format_table(
            ctx.hit_rate,
            ctx.cost_saved,
            headers=["Project", "Date", "Model", "Turns", "Input", "Output", "CacheRead"],
            rows=[
                [r["project"], r["date"], r["model"], r["turns"],
                 r["input_tokens"], r["output_tokens"], r["cache_read_tokens"]]
                for r in rows
            ],
        )


class SessionsListView:
    """--summary --period day: compact per-session list."""

    def render(self, ctx: ReportContext) -> str:
        rows = ctx.conn.execute(f"""
            SELECT
                session_id,
                COALESCE(project, '—')                                      AS project,
                date,
                start_ts,
                end_ts,
                model,
                turns,
                input_tokens + cache_creation_tokens + cache_read_tokens    AS total_tokens,
                cache_read_tokens,
                input_tokens + cache_creation_tokens + cache_read_tokens    AS denom
            FROM sessions
            WHERE {ctx.date_filter}{ctx.model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, ctx.params).fetchall()

        if ctx.as_json:
            return json.dumps({
                "cache_efficiency": {"hit_rate": round(ctx.hit_rate, 4)},
                "rows": [dict(r) for r in rows],
            }, indent=2)

        table_rows = []
        for r in rows:
            denom = r["denom"] or 0
            cache_pct = f"{r['cache_read_tokens'] / denom:.0%}" if denom else "—"
            table_rows.append([
                r["session_id"][:8],
                r["project"],
                r["date"],
                (r["start_ts"] or "")[:19],
                (r["end_ts"] or "")[:19],
                r["turns"],
                r["total_tokens"],
                cache_pct,
            ])

        return _format_table(
            ctx.hit_rate,
            ctx.cost_saved,
            headers=["Session", "Project", "Date", "Start", "End", "Turns", "Tokens", "CacheHit%"],
            rows=table_rows,
        )


class FullDumpView:
    """--detailed: every row in the db, every column, plus sync status."""

    def render(self, ctx: ReportContext) -> str:
        # No date filter by design: --detailed always dumps the whole table.
        # sync_log is folded in via a correlated subquery to avoid aliasing
        # the sessions table (ctx.model_filter references bare column names).
        rows = ctx.conn.execute(f"""
            SELECT
                session_id,
                source,
                model,
                date,
                start_ts,
                end_ts,
                COALESCE(project, '—')  AS project,
                turns,
                tool_calls,
                input_tokens,
                output_tokens,
                cache_creation_tokens,
                cache_read_tokens,
                context_peak_tokens,
                reasoning_tokens,
                context,
                COALESCE((
                    SELECT GROUP_CONCAT(l.store_name, ',')
                    FROM sync_log l
                    WHERE l.session_id = sessions.session_id
                      AND l.source = sessions.source
                      AND l.model = sessions.model
                ), '')                  AS synced
            FROM sessions
            WHERE 1=1{ctx.model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, ctx.params).fetchall()

        if ctx.as_json:
            return json.dumps({
                "cache_efficiency": {
                    "hit_rate": round(ctx.hit_rate, 4),
                    "cost_saved_rate": round(ctx.cost_saved, 4),
                },
                "rows": [dict(r) for r in rows],
            }, indent=2)

        return _format_table(
            ctx.hit_rate,
            ctx.cost_saved,
            headers=["Session", "Source", "Model", "Date", "Start", "End",
                     "Project", "Turns", "Tools", "Input", "Output",
                     "CacheCreate", "CacheRead", "CtxPeak", "Reasoning",
                     "Context", "Synced"],
            rows=[
                [r["session_id"], r["source"], r["model"], r["date"],
                 (r["start_ts"] or "")[:19], (r["end_ts"] or "")[:19],
                 r["project"], r["turns"], r["tool_calls"],
                 r["input_tokens"], r["output_tokens"],
                 r["cache_creation_tokens"], r["cache_read_tokens"],
                 r["context_peak_tokens"], r["reasoning_tokens"],
                 r["context"], r["synced"]]
                for r in rows
            ],
        )


# ── Dispatch ───────────────────────────────────────────────────────────────


def _pick_strategy(
    summary: bool, by_project: bool, period: str, detailed: bool = False
) -> ReportStrategy:
    """Select the correct render strategy from the dispatch axes."""
    if detailed:
        return FullDumpView()
    if by_project:
        return ByProjectView()
    if not summary:
        return SessionsDetailedView()
    if period in ("all", "month", "year"):
        return PeriodSummaryView()
    return SessionsListView()


# ── Core reporter ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UsageReporter:
    db_path: Path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _cache_efficiency(self, conn: sqlite3.Connection) -> tuple[float, float]:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(cache_creation_tokens), 0),
                COALESCE(SUM(cache_read_tokens), 0)
            FROM sessions
        """).fetchone()
        total = row[0] + row[1] + row[2]
        if total == 0:
            return 0.0, 0.0
        hit_rate = row[2] / total
        # cache-read costs ~10% of regular input → ~90% saving on those tokens
        cost_saved = hit_rate * 0.9
        return hit_rate, cost_saved

    def _make_context(
        self,
        conn: sqlite3.Connection,
        period: str,
        models: Sequence[str] | None,
        as_json: bool,
    ) -> ReportContext:
        hit_rate, cost_saved = self._cache_efficiency(conn)
        date_filter = _DATE_RANGE_SQL[period]
        model_filter = ""
        params: list = []
        if models:
            placeholders = ",".join("?" * len(models))
            model_filter = f" AND model IN ({placeholders})"
            params.extend(models)
        return ReportContext(
            conn=conn,
            period=period,
            date_filter=date_filter,
            model_filter=model_filter,
            params=params,
            hit_rate=hit_rate,
            cost_saved=cost_saved,
            as_json=as_json,
        )

    def report(
        self,
        period: str = "day",
        models: Sequence[str] | None = None,
        by_project: bool = False,
        summary: bool = False,
        as_json: bool = False,
        detailed: bool = False,
    ) -> str:
        if period not in _PERIOD_SQL:
            raise ValueError(f"period must be one of {list(_PERIOD_SQL)}")
        conn = self._connect()
        try:
            ctx = self._make_context(conn, period, models, as_json)
            return _pick_strategy(summary, by_project, period, detailed).render(ctx)
        finally:
            conn.close()


# ── Formatting ─────────────────────────────────────────────────────────────


def _format_table(
    hit_rate: float,
    cost_saved: float,
    headers: list[str],
    rows: list[list],
) -> str:
    efficiency_line = (
        f"Cache efficiency: {hit_rate:.0%} read from cache "
        f"(~{cost_saved:.0%} cost saved)\n"
    )
    if not rows:
        return efficiency_line + "No data for this period.\n"

    str_rows = [[str(c) for c in row] for row in rows]
    widths = [
        max(len(h), max((len(r[i]) for r in str_rows), default=0))
        for i, h in enumerate(headers)
    ]
    sep = "  ".join("-" * w for w in widths)
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    data_lines = [
        "  ".join(c.ljust(w) for c, w in zip(row, widths))
        for row in str_rows
    ]
    return efficiency_line + "\n".join([header_line, sep] + data_lines) + "\n"
