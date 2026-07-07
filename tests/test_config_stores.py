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


# ── .env file support ──────────────────────────────────────────────────────


def test_env_file_used_when_var_not_in_environ(monkeypatch, tmp_path):
    env_file = tmp_path / ".tokentracer.env"
    env_file.write_text("FILE_ONLY_VAR=from-file\n", encoding="utf-8")
    monkeypatch.setattr("src.config._ENV_FILE_PATH", env_file)
    monkeypatch.delenv("FILE_ONLY_VAR", raising=False)
    result = _expand_env_vars({"key": "${FILE_ONLY_VAR}"})
    assert result == {"key": "from-file"}


def test_environ_wins_over_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".tokentracer.env"
    env_file.write_text("SHARED_VAR=from-file\n", encoding="utf-8")
    monkeypatch.setattr("src.config._ENV_FILE_PATH", env_file)
    monkeypatch.setenv("SHARED_VAR", "from-environ")
    result = _expand_env_vars({"key": "${SHARED_VAR}"})
    assert result == {"key": "from-environ"}


def test_missing_var_raises_even_with_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".tokentracer.env"
    env_file.write_text("OTHER=x\n", encoding="utf-8")
    monkeypatch.setattr("src.config._ENV_FILE_PATH", env_file)
    monkeypatch.delenv("NOWHERE_VAR", raising=False)
    with pytest.raises(ValueError, match="Missing env var 'NOWHERE_VAR'"):
        _expand_env_vars({"key": "${NOWHERE_VAR}"})


def test_load_env_file_parsing(tmp_path):
    from src.config import _load_env_file

    env_file = tmp_path / "test.env"
    env_file.write_text(
        "# comment line\n"
        "\n"
        "PLAIN=value\n"
        "QUOTED_DOUBLE=\"double quoted\"\n"
        "QUOTED_SINGLE='single quoted'\n"
        "SPACED = padded \n"
        "WITH_EQUALS=a=b=c\n"
        "malformed line without equals\n",
        encoding="utf-8",
    )
    values = _load_env_file(env_file)
    assert values == {
        "PLAIN": "value",
        "QUOTED_DOUBLE": "double quoted",
        "QUOTED_SINGLE": "single quoted",
        "SPACED": "padded",
        "WITH_EQUALS": "a=b=c",
    }


def test_load_env_file_missing_returns_empty(tmp_path):
    from src.config import _load_env_file

    assert _load_env_file(tmp_path / "nope.env") == {}
