from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterator

from .base import to_date, to_local_iso
from ..models import SessionRecord, UNKNOWN_MODEL


class ClaudeCliCollector:
    """Yields one SessionRecord per JSONL conversation file."""

    source = "claude_cli"

    def __init__(self, projects_dir: Path, track_project_names: bool = False) -> None:
        self._dir = projects_dir
        self._track_projects = track_project_names

    def collect(self, since: date) -> Iterator[SessionRecord]:
        for jsonl_path in self._dir.rglob("*.jsonl"):
            # Skip files last modified before the window — their sessions
            # necessarily started before `since` and would be filtered anyway.
            try:
                mtime_date = date.fromtimestamp(jsonl_path.stat().st_mtime)
            except OSError:
                continue
            if mtime_date < since:
                continue
            record = self._parse_session(jsonl_path, since)
            if record is not None:
                yield record

    def _parse_session(self, path: Path, since: date) -> SessionRecord | None:
        session_id = path.stem
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }
        model = UNKNOWN_MODEL
        project: str | None = None
        start_ts: str | None = None
        end_ts: str | None = None
        turns = 0

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = entry.get("timestamp")
            if ts:
                if start_ts is None or ts < start_ts:
                    start_ts = ts
                if end_ts is None or ts > end_ts:
                    end_ts = ts

            if project is None and self._track_projects:
                cwd = entry.get("cwd")
                if cwd:
                    project = Path(cwd).name

            if entry.get("type") != "assistant":
                continue

            turns += 1
            msg = entry.get("message") or {}
            if model == UNKNOWN_MODEL and msg.get("model"):
                model = msg["model"]
            usage = msg.get("usage") or {}
            totals["input_tokens"] += usage.get("input_tokens", 0)
            totals["output_tokens"] += usage.get("output_tokens", 0)
            totals["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0)
            totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)

        # Apply since filter on sessions that have a known start date
        if start_ts is not None:
            session_date = to_date(start_ts)
            if session_date is None or session_date < since:
                return None
            date_str = session_date.isoformat()
        else:
            date_str = date.today().isoformat()

        return SessionRecord(
            session_id=session_id,
            source=self.source,
            model=model,
            date=date_str,
            start_ts=to_local_iso(start_ts) if start_ts else None,
            end_ts=to_local_iso(end_ts) if end_ts else None,
            project=project,
            turns=turns,
            **totals,
        )
