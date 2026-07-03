"""Collector for Claude CLI (claude-code) activity."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Iterator

from .base import file_touched_since, to_local_date
from ..models import ActivityRecord


class ClaudeCliCollector:
    """Collects activity records from Claude CLI JSONL logs.

    Scans a directory for .jsonl files (typically under ~/.claude/projects/)
    and extracts token usage from assistant messages.
    """

    source = "claude-cli"

    def __init__(self, projects_dir: Path | None = None) -> None:
        """Initialize with projects directory containing Claude CLI data.

        Args:
            projects_dir: Path to directory containing conversation JSONL files.
                         If None, defaults to ~/.claude/projects.
        """
        self._dir = projects_dir or Path.home() / ".claude" / "projects"

    def collect(self, since: date) -> Iterator[ActivityRecord]:
        """Collect activity records from Claude CLI logs since the given date.

        Args:
            since: Only include activity from this date or later (local time).

        Yields:
            ActivityRecord objects, one per (date, model) combination.
        """
        if not self._dir.exists():
            return

        # Group token usage by (date, model)
        aggregates: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        )

        # Walk all .jsonl files
        for jsonl_path in self._dir.rglob("*.jsonl"):
            if not file_touched_since(jsonl_path, since):
                continue
            try:
                lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Skip non-assistant messages
                    if entry.get("type") != "assistant":
                        continue

                    # Extract timestamp and model
                    ts = entry.get("timestamp")
                    if not ts:
                        continue

                    msg = entry.get("message", {})
                    model = msg.get("model") or "unknown"
                    usage = msg.get("usage")
                    if not usage:
                        continue

                    # Convert timestamp to local date
                    record_date = to_local_date(ts)

                    # Check if date is >= since
                    if record_date < since.isoformat():
                        continue

                    # Accumulate tokens for this (date, model) pair
                    key = (record_date, model)
                    aggregates[key]["input_tokens"] += usage.get("input_tokens", 0)
                    aggregates[key]["output_tokens"] += usage.get("output_tokens", 0)
                    aggregates[key]["cache_read_tokens"] += usage.get(
                        "cache_read_input_tokens", 0
                    )
                    aggregates[key]["cache_write_tokens"] += usage.get(
                        "cache_creation_input_tokens", 0
                    )
            except OSError:
                # Skip files we can't read
                continue

        # Convert aggregates to ActivityRecord objects
        for (record_date, model), counts in aggregates.items():
            yield ActivityRecord(
                date=record_date,
                source=self.source,
                model=model,
                input_tokens=counts["input_tokens"],
                output_tokens=counts["output_tokens"],
                cache_read_tokens=counts["cache_read_tokens"],
                cache_write_tokens=counts["cache_write_tokens"],
            )
