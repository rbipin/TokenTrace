"""Collector for GitHub Copilot CLI activity.

Three complementary sources under ``~/.copilot``:

* ``session-store.db`` (SQLite) — sessions and turns (scope/repo/timestamp).
* ``session-state/<id>/events.jsonl`` — per-turn model name and token counts.
  For completed sessions the ``session.shutdown`` event carries the full
  ``modelMetrics`` aggregate (inputTokens, outputTokens, cacheReadTokens,
  cacheWriteTokens, reasoningTokens per model).  For active sessions the
  per-turn ``outputTokens`` field on ``assistant.message`` events is summed as
  a fallback.
* ``logs/process-*.log`` — ``CompactionProcessor`` utilization lines giving the
  daily context-window peak (max tokens held in the model's context window).

The daily context-window peak is folded into the most-active row for that day
rather than emitted as a separate orphan record.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

from ..models import UNKNOWN_MODEL, ActivityRecord, merge_records
from .base import file_touched_since, to_local_date, to_local_iso

_UTILIZATION_RE = re.compile(r"Utilization\s+[\d.]+%\s+\((\d+)/\d+\s+tokens\)")
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)")


class CopilotCliCollector:
    """Reads the Copilot CLI's local session store, events, and process logs."""

    source = "copilot-cli"

    def __init__(self, copilot_home: Path) -> None:
        self._home = Path(copilot_home)

    def collect(self, since: date) -> Iterable[ActivityRecord]:
        session_records = merge_records(list(self._from_session_store(since)))
        peaks = self._daily_peaks(since)
        return self._apply_peaks(session_records, peaks)

    def _apply_peaks(
        self, records: list[ActivityRecord], peaks: dict[str, int]
    ) -> list[ActivityRecord]:
        """Attach each day's context-window peak to its most-active row.

        If no session records exist for a day (peak logged but no session
        started), a standalone peak-only row is emitted instead.
        """
        by_day: dict[str, list[ActivityRecord]] = {}
        for rec in records:
            by_day.setdefault(rec.date, []).append(rec)

        out: list[ActivityRecord] = list(records)
        for day, peak in peaks.items():
            if day in by_day:
                best = max(by_day[day], key=lambda r: r.prompts)
                idx = out.index(best)
                out[idx] = replace(best, context_peak_tokens=max(best.context_peak_tokens, peak))
            else:
                out.append(ActivityRecord(
                    date=day, source=self.source, model=UNKNOWN_MODEL,
                    scope="", context_peak_tokens=peak,
                ))
        return out

    # -- session-store.db + events.jsonl -----------------------------------
    def _from_session_store(self, since: date) -> Iterator[ActivityRecord]:
        db = self._home / "session-store.db"
        if not db.is_file():
            return

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                """
                SELECT t.timestamp, t.session_id,
                       COALESCE(NULLIF(s.repository, ''), s.cwd, '') AS scope
                FROM turns t
                LEFT JOIN sessions s ON s.id = t.session_id
                WHERE t.timestamp IS NOT NULL
                """
            ).fetchall()
        except sqlite3.Error:
            return
        finally:
            conn.close()

        since_iso = since.isoformat()
        # Aggregate turn metadata per session within the lookback window.
        sessions: dict[str, dict] = {}
        for ts, session_id, scope in rows:
            day = to_local_date(ts)
            if day < since_iso:
                continue
            iso = to_local_iso(ts)
            info = sessions.setdefault(
                session_id,
                {"day": day, "scope": scope or "", "turns": 0, "first": iso, "last": iso},
            )
            info["turns"] += 1
            info["first"] = min(info["first"], iso)
            info["last"] = max(info["last"], iso)

        for session_id, info in sessions.items():
            yield from self._records_for_session(
                session_id, info["scope"], info["day"],
                info["turns"], info["first"], info["last"],
            )

    def _records_for_session(
        self,
        session_id: str,
        scope: str,
        day: str,
        turns: int,
        first_ts: str,
        last_ts: str,
    ) -> Iterator[ActivityRecord]:
        """Yield one or more ActivityRecords for a session.

        For completed sessions (``session.shutdown`` present) the full
        per-model token breakdown from ``modelMetrics`` is used.  For active
        sessions the per-turn ``outputTokens`` from ``assistant.message``
        events is summed as a fallback, keeping a single record with the
        plurality model.
        """
        events_file = self._home / "session-state" / session_id / "events.jsonl"
        if not events_file.is_file():
            yield ActivityRecord(
                date=day, source=self.source, model=UNKNOWN_MODEL, scope=scope,
                sessions=1, prompts=turns, turns=turns,
                first_ts=first_ts, last_ts=last_ts,
            )
            return

        shutdown_data: dict | None = None
        model_counts: dict[str, int] = {}
        output_tokens_fallback: int = 0

        try:
            with events_file.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    ev_type = ev.get("type")
                    if ev_type == "session.shutdown":
                        shutdown_data = ev.get("data", {})
                    elif ev_type == "assistant.message":
                        data = ev.get("data", {})
                        model = data.get("model") or ""
                        if model:
                            model_counts[model] = model_counts.get(model, 0) + 1
                        output_tokens_fallback += data.get("outputTokens", 0)
        except OSError:
            pass

        if shutdown_data is not None:
            yield from self._records_from_shutdown(
                shutdown_data, scope, day, turns, first_ts, last_ts
            )
            return

        # Fallback: active session — use plurality model + summed outputTokens.
        model = (
            max(model_counts, key=lambda m: (model_counts[m], m))
            if model_counts
            else UNKNOWN_MODEL
        )
        yield ActivityRecord(
            date=day, source=self.source, model=model, scope=scope,
            sessions=1, prompts=turns, turns=turns,
            output_tokens=output_tokens_fallback,
            first_ts=first_ts, last_ts=last_ts,
        )

    @classmethod
    def _records_from_shutdown(
        cls,
        sd: dict,
        scope: str,
        day: str,
        turns: int,
        first_ts: str,
        last_ts: str,
    ) -> Iterator[ActivityRecord]:
        """Yield per-model ActivityRecords from a session.shutdown payload."""
        model_metrics: dict[str, dict] = sd.get("modelMetrics", {})
        if not model_metrics:
            return

        # Attribute session/turn counts to the model with most output tokens.
        primary = max(
            model_metrics,
            key=lambda m: model_metrics[m].get("usage", {}).get("outputTokens", 0),
        )

        for model_name, m in model_metrics.items():
            usage = m.get("usage", {})
            is_primary = model_name == primary
            yield ActivityRecord(
                date=day,
                source="copilot-cli",
                model=model_name,
                scope=scope,
                sessions=1 if is_primary else 0,
                prompts=turns if is_primary else 0,
                turns=turns if is_primary else 0,
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                cache_read_tokens=usage.get("cacheReadTokens", 0),
                cache_write_tokens=usage.get("cacheWriteTokens", 0),
                reasoning_tokens=usage.get("reasoningTokens", 0),
                first_ts=first_ts if is_primary else None,
                last_ts=last_ts if is_primary else None,
            )

    # -- process logs (context-window peak) --------------------------------
    def _daily_peaks(self, since: date) -> dict[str, int]:
        """Return {YYYY-MM-DD: max_tokens} from all process logs since ``since``."""
        logs_dir = self._home / "logs"
        if not logs_dir.is_dir():
            return {}
        peaks: dict[str, int] = {}
        since_iso = since.isoformat()
        for log in logs_dir.glob("process-*.log"):
            if not file_touched_since(log, since):
                continue
            for day, tokens in self._scan_log(log):
                if day < since_iso:
                    continue
                peaks[day] = max(peaks.get(day, 0), tokens)
        return peaks

    @staticmethod
    def _scan_log(path: Path) -> Iterator[tuple[str, int]]:
        try:
            with path.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    m = _UTILIZATION_RE.search(line)
                    if not m:
                        continue
                    ts = _LOG_TS_RE.match(line)
                    if not ts:
                        continue
                    yield to_local_date(ts.group(1)), int(m.group(1))
        except OSError:
            return

