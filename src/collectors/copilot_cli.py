from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterator

from .base import to_date, to_local_iso
from ..models import SessionRecord, UNKNOWN_MODEL

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)")


class CopilotCliCollector:
    """Yields one SessionRecord per (Copilot session id, model)."""

    source = "copilot_cli"

    def __init__(self, copilot_home: Path, track_project_names: bool = False) -> None:
        self._home = copilot_home
        self._track_projects = track_project_names

    def collect(self, since: date) -> Iterator[SessionRecord]:
        db_path = self._home / "session-store.db"
        if not db_path.exists():
            return

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, cwd, repository, startedAt, endedAt FROM sessions"
            ).fetchall()
        except sqlite3.OperationalError:
            conn.close()
            return
        conn.close()

        for row in rows:
            session_id: str = row["id"]
            start_ts = row["startedAt"]
            end_ts = row["endedAt"]

            session_date = to_date(start_ts)
            if session_date is None or session_date < since:
                continue

            project: str | None = None
            if self._track_projects:
                repo: str = row["repository"] or ""
                cwd: str = row["cwd"] or ""
                if repo:
                    project = repo.split("/")[-1]
                elif cwd:
                    project = Path(cwd).name

            date_str = session_date.isoformat()
            start_iso = to_local_iso(start_ts)
            end_iso = to_local_iso(end_ts)

            yield from self._parse_events(
                session_id, date_str, start_iso, end_iso, project
            )

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
            if event_type == "session.shutdown":
                yield from self._from_shutdown(
                    event, session_id, date_str, start_ts, end_ts, project
                )
                return
            if event_type != "assistant.message":
                continue
            turns += 1
            if model == UNKNOWN_MODEL and event.get("model"):
                model = event["model"]
            usage = event.get("usage") or {}
            totals["input_tokens"] += usage.get("inputTokens", 0)
            totals["output_tokens"] += usage.get("outputTokens", 0)
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
        self, event: dict, session_id: str,
        date_str: str, start_ts: str | None, end_ts: str | None,
        project: str | None,
    ) -> Iterator[SessionRecord]:
        metrics: dict = event.get("modelMetrics") or {}
        for model, m in metrics.items():
            yield SessionRecord(
                session_id=session_id,
                source=self.source,
                model=model or UNKNOWN_MODEL,
                date=date_str,
                start_ts=start_ts,
                end_ts=end_ts,
                project=project,
                turns=m.get("turns", 0),
                input_tokens=m.get("inputTokens", 0),
                output_tokens=m.get("outputTokens", 0),
                cache_read_tokens=m.get("cacheReadTokens", 0),
                cache_creation_tokens=m.get("cacheWriteTokens", 0),
                reasoning_tokens=m.get("reasoningTokens", 0),
            )
