from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Iterator

from .base import to_date, to_local_iso
from ..models import SessionRecord, UNKNOWN_MODEL

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)")


class CopilotCliCollector:
    """Yields one SessionRecord per (Copilot session id, model)."""

    source = "copilot_cli"

    def __init__(self, copilot_home: Path, resolver=None) -> None:
        """Args:
            copilot_home: Root of the ~/.copilot data directory.
            resolver: Optional ProjectNameResolver; when None, records carry
                no project identity.
        """
        self._home = copilot_home
        self._resolver = resolver

    def collect(self, since: date) -> Iterator[SessionRecord]:
        db_path = self._home / "session-store.db"
        if not db_path.exists():
            return

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = self._query_sessions(conn)
        except sqlite3.OperationalError as exc:
            print(
                f"Warning [copilot_cli]: could not read sessions table: {exc}",
                file=sys.stderr,
            )
            return
        finally:
            conn.close()

        for row in rows:
            session_id: str = row["id"]
            start_ts = row["start_ts"]
            end_ts = row["end_ts"]

            session_date = to_date(start_ts)
            if session_date is None or session_date < since:
                continue

            project: str | None = None
            if self._resolver is not None:
                repo: str = row["repository"] or ""
                cwd: str = row["cwd"] or ""
                display_name = repo.split("/")[-1] if repo else (Path(cwd).name or None)
                project = self._resolver.resolve(display_name, cwd or None)

            date_str = session_date.isoformat()
            start_iso = to_local_iso(start_ts)
            end_iso = to_local_iso(end_ts)

            yield from self._parse_events(
                session_id, date_str, start_iso, end_iso, project
            )

    @staticmethod
    def _query_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
        """Fetch sessions, supporting both old and new Copilot CLI schemas.

        Old schema: startedAt / endedAt columns.
        New schema (Copilot CLI >= mid-2026): created_at / updated_at.
        """
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        if {"startedAt", "endedAt"} <= cols:
            start_col, end_col = "startedAt", "endedAt"
        elif {"created_at", "updated_at"} <= cols:
            start_col, end_col = "created_at", "updated_at"
        else:
            raise sqlite3.OperationalError(
                f"unrecognized sessions schema (columns: {sorted(cols)})"
            )
        return conn.execute(
            f"SELECT id, cwd, repository, "
            f"{start_col} AS start_ts, {end_col} AS end_ts FROM sessions"
        ).fetchall()

    def _parse_events(
        self,
        session_id: str,
        date_str: str,
        start_ts: str | None,
        end_ts: str | None,
        project: str | None,
    ) -> Iterator[SessionRecord]:
        events_path = self._home / "session-state" / session_id / "events.jsonl"
        if not events_path.exists():
            return

        # Single pass: prefer the shutdown event (per-model breakdown), while
        # accumulating assistant-message totals as the fallback.
        totals = dict(input_tokens=0, output_tokens=0, cache_read_tokens=0,
                      cache_creation_tokens=0, reasoning_tokens=0)
        turns = 0
        model = UNKNOWN_MODEL

        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            payload = event.get("data") or event  # new CLI nests under "data"
            if event_type == "session.shutdown":
                yield from self._from_shutdown(
                    payload, session_id, date_str, start_ts, end_ts, project
                )
                return
            if event_type != "assistant.message":
                continue
            turns += 1
            if model == UNKNOWN_MODEL and payload.get("model"):
                model = payload["model"]
            usage = payload.get("usage") or {}
            totals["input_tokens"] += usage.get("inputTokens", 0)
            totals["output_tokens"] += usage.get(
                "outputTokens", payload.get("outputTokens", 0)
            )
            totals["cache_read_tokens"] += usage.get("cacheReadTokens", 0)
            totals["cache_creation_tokens"] += usage.get("cacheWriteTokens", 0)
            totals["reasoning_tokens"] += usage.get("reasoningTokens", 0)

        yield SessionRecord(
            session_id=session_id,
            source=self.source,
            model=model,
            date=date_str,
            start_ts=start_ts,
            end_ts=end_ts,
            project=project,
            turns=turns,
            **totals,
        )

    def _from_shutdown(
        self, payload: dict, session_id: str,
        date_str: str, start_ts: str | None, end_ts: str | None,
        project: str | None,
    ) -> Iterator[SessionRecord]:
        metrics: dict = payload.get("modelMetrics") or {}
        for model, m in metrics.items():
            # Old format: token counts flat on the metric dict, plus "turns".
            # New format: counts nested under "usage", turns under requests.count.
            usage = m.get("usage") or m
            turns = m.get("turns") or (m.get("requests") or {}).get("count", 0)
            yield SessionRecord(
                session_id=session_id,
                source=self.source,
                model=model or UNKNOWN_MODEL,
                date=date_str,
                start_ts=start_ts,
                end_ts=end_ts,
                project=project,
                turns=turns,
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                cache_read_tokens=usage.get("cacheReadTokens", 0),
                cache_creation_tokens=usage.get("cacheWriteTokens", 0),
                reasoning_tokens=usage.get("reasoningTokens", 0),
            )
