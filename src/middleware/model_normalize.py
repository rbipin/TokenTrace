"""Middleware that fills in SessionRecord.canonical_model."""
from __future__ import annotations

from dataclasses import replace

from ..model_normalize import normalize_model
from ..models import SessionRecord


class ModelNormalizeMiddleware:
    """Populates canonical_model via normalize_model() for every record."""

    name = "model_normalize"

    def applies(self, records: list[SessionRecord]) -> bool:
        return True

    def process(self, records: list[SessionRecord]) -> list[SessionRecord]:
        return [
            replace(r, canonical_model=normalize_model(r.model, r.source))
            for r in records
        ]
