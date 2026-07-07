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
