from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.config import Config, write_toml_setting


def test_default_track_project_names_is_false():
    cfg = Config()
    assert cfg.track_project_names is False


def test_load_returns_defaults_when_no_toml(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / "no_such.toml")
    cfg = Config.load()
    assert cfg.track_project_names is False


def test_load_reads_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = true\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names is True


def test_load_override_wins_over_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = true\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load(track_project_names=False)
    assert cfg.track_project_names is False


def test_load_invalid_toml_falls_back_to_defaults(tmp_path, monkeypatch, capsys):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("NOT VALID TOML @@@@")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names is False
    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_write_toml_setting_creates_file(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    write_toml_setting("track_project_names", True)
    assert toml.exists()
    content = toml.read_text()
    assert "track_project_names = true" in content


def test_write_toml_setting_updates_existing(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = false\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    write_toml_setting("track_project_names", True)
    content = toml.read_text()
    assert "track_project_names = true" in content
