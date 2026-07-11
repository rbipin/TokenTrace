"""Fluent pipeline wiring collectors to stores."""

from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import date
from typing import List

from .collectors.base import ActivityCollector
from .middleware.base import RecordMiddleware
from .models import DEFAULT_CONTEXT, SessionRecord, merge_records
from .stores import SessionStore


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single pipeline run."""

    records_written: int
    collectors_run: int
    errors: List[str] = field(default_factory=list)
    stores_failed: List[str] = field(default_factory=list)


class TrackerPipeline:
    """Builds and runs a collection pass.

    Usage::

        (TrackerPipeline()
            .add(ClaudeCliCollector(...))
            .since(start)
            .stores(SqliteStore(db), remote_store)
            .run())
    """

    def __init__(self) -> None:
        self._collectors: list[ActivityCollector] = []
        self._since: date | None = None
        self._stores: list[SessionStore] = []
        self._context: str = DEFAULT_CONTEXT
        self._middlewares: list[RecordMiddleware] = []

    def context(self, label: str) -> "TrackerPipeline":
        """Set the usage context label (e.g. "work" or "personal") stamped on every record."""
        self._context = label
        return self

    def add(self, collector: ActivityCollector) -> "TrackerPipeline":
        self._collectors.append(collector)
        return self

    def middlewares(self, *mw: RecordMiddleware) -> "TrackerPipeline":
        self._middlewares = list(mw)
        return self

    def since(self, start: date) -> "TrackerPipeline":
        self._since = start
        return self

    def stores(self, *stores: SessionStore) -> "TrackerPipeline":
        self._stores = list(stores)
        return self

    def store(self, store: SessionStore) -> "TrackerPipeline":
        warnings.warn(
            "TrackerPipeline.store() is deprecated; use .stores()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.stores(store)

    def run(self) -> RunResult:
        if self._since is None:
            raise ValueError("since(start) must be set before run()")
        if not self._stores:
            raise ValueError("stores(...) must be set before run()")

        records: list[SessionRecord] = []
        errors: list[str] = []

        def _collect(collector: ActivityCollector) -> tuple[list[SessionRecord], str | None]:
            try:
                return list(collector.collect(self._since)), None
            except Exception as exc:
                name = getattr(collector, "source", type(collector).__name__)
                return [], f"{name}: {exc}"

        with ThreadPoolExecutor(max_workers=max(len(self._collectors), 1)) as pool:
            futures = {pool.submit(_collect, c): c for c in self._collectors}
            for future in as_completed(futures):
                recs, err = future.result()
                records.extend(recs)
                if err:
                    errors.append(err)

        merged = merge_records(records)
        merged = [replace(rec, context=self._context) for rec in merged]

        for mw in self._middlewares:
            if mw.applies(merged):
                merged = mw.process(merged)

        # SQLite (first store) must succeed — exceptions propagate
        written = self._stores[0].upsert(merged)
        self._stores[0].close()

        # Remotes: parallel, log-and-continue
        stores_failed: list[str] = []

        def _push(store: SessionStore) -> str | None:
            try:
                store.upsert(merged)
                store.close()
                if hasattr(self._stores[0], "mark_synced"):
                    self._stores[0].mark_synced(merged, store.name)
                return None
            except Exception as exc:
                return f"{store.name}: {exc}"

        if len(self._stores) > 1:
            with ThreadPoolExecutor(max_workers=len(self._stores) - 1) as pool:
                for err in pool.map(_push, self._stores[1:]):
                    if err:
                        stores_failed.append(err)

        return RunResult(
            records_written=written,
            collectors_run=len(self._collectors),
            errors=errors,
            stores_failed=stores_failed,
        )
