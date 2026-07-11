from __future__ import annotations

from src.middleware.model_normalize import ModelNormalizeMiddleware
from src.models import SessionRecord


def _rec(model: str, source: str = "claude_cli") -> SessionRecord:
    return SessionRecord(session_id="s1", source=source, model=model, date="2026-07-01")


def test_applies_always_true():
    mw = ModelNormalizeMiddleware()
    assert mw.applies([_rec("claude-sonnet-4-6")]) is True
    assert mw.applies([]) is True


def test_process_sets_canonical_model_from_date_suffix():
    mw = ModelNormalizeMiddleware()
    [result] = mw.process([_rec("claude-haiku-4-5-20251001")])
    assert result.canonical_model == "claude-haiku-4-5"


def test_process_sets_canonical_model_from_lookup():
    mw = ModelNormalizeMiddleware()
    [result] = mw.process([_rec("claude-sonnet-4.5", source="copilot_cli")])
    assert result.canonical_model == "claude-sonnet-4-5"


def test_process_passthrough_for_unrecognized_model():
    mw = ModelNormalizeMiddleware()
    [result] = mw.process([_rec("gpt-4o", source="copilot_cli")])
    assert result.canonical_model == "gpt-4o"


def test_process_preserves_raw_model():
    mw = ModelNormalizeMiddleware()
    [result] = mw.process([_rec("claude-haiku-4-5-20251001")])
    assert result.model == "claude-haiku-4-5-20251001"


def test_process_handles_batch_of_multiple_records():
    mw = ModelNormalizeMiddleware()
    records = [_rec("claude-haiku-4-5-20251001"), _rec("claude-sonnet-4-6")]
    results = mw.process(records)
    assert [r.canonical_model for r in results] == ["claude-haiku-4-5", "claude-sonnet-4-6"]
