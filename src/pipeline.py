"""Fluent pipeline wiring collectors to the store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import List

from .collectors.base import ActivityCollector
from .models import SessionRecord, merge_records
from .stores import SessionStore
from .stores.sqlite import SqliteStore


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single pipeline run."""

    records_written: int
    collectors_run: int
    errors: List[str] = field(default_factory=list)


class TrackerPipeline:
    """Builds and runs a collection pass.

    Usage::

        (TrackerPipeline()
            .add(CopilotCliCollector(...))
            .since(start)
            .store(SqliteStore(db))
            .run())
    """

    def __init__(self) -> None:
        self._collectors: list[ActivityCollector] = []
        self._since: date | None = None
        self._store: SessionStore | None = None

    def add(self, collector: ActivityCollector) -> "TrackerPipeline":
        self._collectors.append(collector)
        return self

    def since(self, start: date) -> "TrackerPipeline":
        self._since = start
        return self

    def store(self, store: SessionStore) -> "TrackerPipeline":
        self._store = store
        return self

    def run(self) -> RunResult:
        if self._since is None:
            raise ValueError("since(start) must be set before run()")
        if self._store is None:
            raise ValueError("store(store) must be set before run()")

        records: list[SessionRecord] = []
        errors: list[str] = []

        def _collect(collector: ActivityCollector) -> tuple[list[SessionRecord], str | None]:
            try:
                return list(collector.collect(self._since)), None
            except Exception as exc:
                name = getattr(collector, "source", type(collector).__name__)
                return [], f"{name}: {exc}"

        with ThreadPoolExecutor(max_workers=len(self._collectors)) as pool:
            futures = {pool.submit(_collect, c): c for c in self._collectors}
            for future in as_completed(futures):
                recs, err = future.result()
                records.extend(recs)
                if err:
                    errors.append(err)

        merged = merge_records(records)
        written = self._store.upsert(merged)
        return RunResult(records_written=written, collectors_run=len(self._collectors), errors=errors)
