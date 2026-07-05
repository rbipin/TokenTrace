from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_PERIOD_SQL = {
    "day":   "date",
    "month": "strftime('%Y-%m', date)",
    "year":  "strftime('%Y', date)",
}

_DATE_RANGE_SQL = {
    "day":   "date = date('now', 'localtime')",
    "month": "date >= date('now', 'start of month', 'localtime')",
    "year":  "date >= date('now', 'start of year', 'localtime')",
}


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

    def report(
        self,
        period: str = "day",
        models: Sequence[str] | None = None,
        by_project: bool = False,
        summary: bool = False,
        as_json: bool = False,
    ) -> str:
        if period not in _PERIOD_SQL:
            raise ValueError(f"period must be one of {list(_PERIOD_SQL)}")

        conn = self._connect()
        try:
            hit_rate, cost_saved = self._cache_efficiency(conn)
            date_filter = _DATE_RANGE_SQL[period]
            model_filter = ""
            params: list = []
            if models:
                placeholders = ",".join("?" * len(models))
                model_filter = f" AND model IN ({placeholders})"
                params.extend(models)

            args = (conn, date_filter, model_filter, params, hit_rate, cost_saved, as_json)

            if summary:
                if by_project:
                    return self._render_by_project(*args)
                if period in ("month", "year"):
                    return self._render_default(conn, period, date_filter, model_filter, params,
                                                hit_rate, cost_saved, as_json)
                # --summary alone (day): compact per-session view
                return self._render_sessions(*args)

            if by_project:
                return self._render_by_project(*args)

            # Default: detailed per-session view
            return self._render_sessions_detailed(*args)
        finally:
            conn.close()

    def _render_sessions_detailed(
        self, conn, date_filter, model_filter, params,
        hit_rate, cost_saved, as_json,
    ) -> str:
        """Default view: one row per session, full token breakdown."""
        rows = conn.execute(f"""
            SELECT
                COALESCE(project, '—')  AS project,
                source,
                model,
                start_ts,
                end_ts,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_creation_tokens,
                turns,
                input_tokens + cache_creation_tokens + cache_read_tokens AS denom
            FROM sessions
            WHERE {date_filter}{model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, params).fetchall()

        if as_json:
            return json.dumps({
                "cache_efficiency": {
                    "hit_rate": round(hit_rate, 4),
                    "cost_saved_rate": round(cost_saved, 4),
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
                r["cache_read_tokens"],
                r["cache_creation_tokens"],
                cache_pct,
                r["turns"],
            ])

        return self._format_table(
            hit_rate, cost_saved,
            headers=["Project", "Source", "Model", "Start", "End",
                     "Input", "Output", "CacheRead", "CacheCreate", "CacheHit%", "Turns"],
            rows=table_rows,
        )

    def _render_default(
        self, conn, period, date_filter, model_filter, params,
        hit_rate, cost_saved, as_json,
    ) -> str:
        """--summary --period month/year: aggregated roll-up grouped by period+model."""
        period_expr = _PERIOD_SQL[period]
        rows = conn.execute(f"""
            SELECT
                {period_expr}                            AS period,
                source,
                model,
                SUM(turns)                               AS turns,
                SUM(input_tokens)                        AS input_tokens,
                SUM(output_tokens)                       AS output_tokens,
                SUM(cache_creation_tokens)               AS cache_creation_tokens,
                SUM(cache_read_tokens)                   AS cache_read_tokens
            FROM sessions
            WHERE {date_filter}{model_filter}
            GROUP BY {period_expr}, source, model
            ORDER BY period DESC, input_tokens DESC
        """, params).fetchall()

        if as_json:
            return json.dumps({
                "cache_efficiency": {
                    "hit_rate": round(hit_rate, 4),
                    "cost_saved_rate": round(cost_saved, 4),
                },
                "rows": [dict(r) for r in rows],
            }, indent=2)

        return self._format_table(
            hit_rate, cost_saved,
            headers=["Period", "Source", "Model", "Turns",
                     "Input", "Output", "CacheCreate", "CacheRead"],
            rows=[[r["period"], r["source"], r["model"], r["turns"],
                   r["input_tokens"], r["output_tokens"],
                   r["cache_creation_tokens"], r["cache_read_tokens"]]
                  for r in rows],
        )

    def _render_by_project(
        self, conn, date_filter, model_filter, params,
        hit_rate, cost_saved, as_json,
    ) -> str:
        rows = conn.execute(f"""
            SELECT
                project,
                date,
                model,
                SUM(turns)                AS turns,
                SUM(input_tokens)         AS input_tokens,
                SUM(output_tokens)        AS output_tokens,
                SUM(cache_read_tokens)    AS cache_read_tokens
            FROM sessions
            WHERE project IS NOT NULL
              AND {date_filter}{model_filter}
            GROUP BY project, date, model
            ORDER BY SUM(input_tokens + cache_read_tokens) DESC
        """, params).fetchall()

        if not rows:
            note = (
                "No project data found. Enable project tracking:\n"
                "  python3 tracker.py config set track_project_names true\n"
                "Then re-collect: python3 tracker.py collect --track-projects"
            )
            if as_json:
                return json.dumps({"note": note, "rows": []}, indent=2)
            return note

        if as_json:
            return json.dumps({
                "cache_efficiency": {"hit_rate": round(hit_rate, 4)},
                "rows": [dict(r) for r in rows],
            }, indent=2)

        return self._format_table(
            hit_rate, cost_saved,
            headers=["Project", "Date", "Model", "Turns",
                     "Input", "Output", "CacheRead"],
            rows=[[r["project"], r["date"], r["model"], r["turns"],
                   r["input_tokens"], r["output_tokens"], r["cache_read_tokens"]]
                  for r in rows],
        )

    def _render_sessions(
        self, conn, date_filter, model_filter, params,
        hit_rate, cost_saved, as_json,
    ) -> str:
        """--summary (day): compact per-session view."""
        rows = conn.execute(f"""
            SELECT
                session_id,
                COALESCE(project, '—') AS project,
                date,
                start_ts,
                end_ts,
                model,
                turns,
                input_tokens + cache_creation_tokens + cache_read_tokens AS total_tokens,
                cache_read_tokens,
                input_tokens + cache_creation_tokens + cache_read_tokens AS denom
            FROM sessions
            WHERE {date_filter}{model_filter}
            ORDER BY COALESCE(start_ts, date) DESC
        """, params).fetchall()

        if as_json:
            return json.dumps({
                "cache_efficiency": {"hit_rate": round(hit_rate, 4)},
                "rows": [dict(r) for r in rows],
            }, indent=2)

        table_rows = []
        for r in rows:
            sid = r["session_id"][:8]
            denom = r["denom"] or 0
            cache_pct = f"{r['cache_read_tokens'] / denom:.0%}" if denom else "—"
            table_rows.append([
                sid, r["project"], r["date"],
                (r["start_ts"] or "")[:19], (r["end_ts"] or "")[:19],
                r["turns"], r["total_tokens"], cache_pct,
            ])

        return self._format_table(
            hit_rate, cost_saved,
            headers=["Session", "Project", "Date", "Start", "End",
                     "Turns", "Tokens", "CacheHit%"],
            rows=table_rows,
        )

    @staticmethod
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
        widths = [max(len(h), max((len(r[i]) for r in str_rows), default=0))
                  for i, h in enumerate(headers)]
        sep = "  ".join("-" * w for w in widths)
        header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
        data_lines = [
            "  ".join(c.ljust(w) for c, w in zip(row, widths))
            for row in str_rows
        ]
        return efficiency_line + "\n".join([header_line, sep] + data_lines) + "\n"
