from __future__ import annotations

from src.models import UNKNOWN_MODEL, SessionRecord, merge_records


def _rec(**kwargs) -> SessionRecord:
    defaults = dict(session_id="sess-1", source="claude_cli", model="claude-sonnet-4-6")
    defaults.update(kwargs)
    return SessionRecord(**defaults)


def test_key_is_session_source_model():
    r = _rec(session_id="abc", source="copilot_cli", model="gpt-4o")
    assert r.key == ("abc", "copilot_cli", "gpt-4o")


def test_default_fields():
    r = SessionRecord(session_id="s1", source="claude_cli")
    assert r.model == UNKNOWN_MODEL
    assert r.project is None
    assert r.turns == 0
    assert r.input_tokens == 0
    assert r.cache_creation_tokens == 0
    assert r.cache_read_tokens == 0
    assert r.start_ts is None
    assert r.end_ts is None


def test_merge_deduplicates_by_key():
    a = _rec(session_id="s1", turns=3)
    b = _rec(session_id="s1", turns=5)  # same key, different turns
    result = merge_records([a, b])
    assert len(result) == 1
    assert result[0].turns == 5  # last writer wins


def test_merge_keeps_distinct_keys():
    a = _rec(session_id="s1", model="claude-sonnet-4-6")
    b = _rec(session_id="s1", model="claude-opus-4-8")  # same session, different model
    c = _rec(session_id="s2", model="claude-sonnet-4-6")
    result = merge_records([a, b, c])
    assert len(result) == 3


def test_merge_empty():
    assert merge_records([]) == []
