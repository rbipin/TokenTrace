from __future__ import annotations
import textwrap
from pathlib import Path
import pytest
from src.config import Config, StoreConfig


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".tokentracer.toml"
    p.write_text(textwrap.dedent(content))
    return p


def test_no_stores_section(tmp_path, monkeypatch):
    toml = _write_toml(tmp_path, "[tracking]\ntrack_project_names = false\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.remote_stores == ()


def test_stores_parsed(tmp_path, monkeypatch):
    toml = _write_toml(tmp_path, """
        [stores.supabase]
        url = "https://example.supabase.co"
        api_key = "secret"

        [stores.mystore]
        class = "mypackage.MyStore"
        endpoint = "https://internal"
    """)
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert len(cfg.remote_stores) == 2

    sup = next(s for s in cfg.remote_stores if s.name == "supabase")
    assert sup.class_path is None
    assert sup.params == {"url": "https://example.supabase.co", "api_key": "secret"}

    my = next(s for s in cfg.remote_stores if s.name == "mystore")
    assert my.class_path == "mypackage.MyStore"
    assert my.params == {"endpoint": "https://internal"}


def test_sqlite_section_excluded(tmp_path, monkeypatch):
    toml = _write_toml(tmp_path, "[stores.sqlite]\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.remote_stores == ()


import os
import pytest
from src.config import _expand_env_vars


def test_expand_env_vars_substitutes_known_var(monkeypatch):
    monkeypatch.setenv("MY_URL", "https://example.supabase.co")
    result = _expand_env_vars({"url": "${MY_URL}", "other": "literal"})
    assert result == {"url": "https://example.supabase.co", "other": "literal"}


def test_expand_env_vars_raises_for_missing_var(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(ValueError, match="Missing env var 'MISSING_VAR'"):
        _expand_env_vars({"key": "${MISSING_VAR}"})


def test_expand_env_vars_passthrough_non_string():
    result = _expand_env_vars({"count": 42, "flag": True})
    assert result == {"count": 42, "flag": True}


def test_expand_env_vars_passthrough_no_placeholder():
    result = _expand_env_vars({"url": "https://literal.com"})
    assert result == {"url": "https://literal.com"}


def test_instantiate_store_expands_env_vars(monkeypatch, tmp_path):
    """instantiate_store passes expanded params to the store constructor."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "usage.db"))
    from src.stores.registry import instantiate_store
    store = instantiate_store(
        "sqlite",
        {"db_path": "${SQLITE_PATH}"},
        class_path="src.stores.sqlite.SqliteStore",
    )
    store.close()
