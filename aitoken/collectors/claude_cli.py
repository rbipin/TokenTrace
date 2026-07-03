"""Collector for Claude CLI (claude-code) activity."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from aitoken.collectors.base import to_local_date
from aitoken.models import ActivityRecord


class ClaudeCliCollector:
    """Collects activity records from Claude CLI JSONL logs.

    Scans a directory for .jsonl files (typically under ~/.claude/conversations/)
    and extracts token usage from assistant messages.
    """

    def __init__(self, root: Path) -> None:
        """Initialize with root directory containing Claude CLI data.

        Args:
            root: Path to directory containing conversation JSONL files.
                  Structure is typically project_name/conv.jsonl
        """
        self.root = Path(root)

    def collect(self, since: date) -> list[ActivityRecord]:
        """Collect activity records from Claude CLI logs since the given date.

        Args:
            since: Only include activity from this date or later (local time).

        Returns:
            List of ActivityRecord objects, one per (date, model) combination.
        """
        if not self.root.exists():
            return []

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
        for jsonl_path in self.root.rglob("*.jsonl"):
            try:
                with open(jsonl_path, encoding="utf-8") as f:
                    for line in f:
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
                        model = msg.get("model", "unknown")
                        usage = msg.get("usage", {})

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
            except (OSError, IOError):
                # Skip files we can't read
                continue

        # Convert aggregates to ActivityRecord objects
        records = []
        for (record_date, model), counts in aggregates.items():
            records.append(
                ActivityRecord(
                    date=record_date,
                    source="claude-cli",
                    model=model,
                    input_tokens=counts["input_tokens"],
                    output_tokens=counts["output_tokens"],
                    cache_read_tokens=counts["cache_read_tokens"],
                    cache_write_tokens=counts["cache_write_tokens"],
                )
            )

        return records
