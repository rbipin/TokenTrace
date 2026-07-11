from __future__ import annotations

from pathlib import Path

import pytest

from src import config
from src.config import Config, write_toml_setting


def test_toml_path_lives_inside_tokentracer_folder():
    assert config._TOML_PATH == Path.home() / ".tokentracer" / ".tokentracer.toml"


def test_env_file_path_lives_inside_tokentracer_folder():
    assert config._ENV_FILE_PATH == Path.home() / ".tokentracer" / ".tokentracer.env"


def test_default_track_project_names_is_no():
    cfg = Config()
    assert cfg.track_project_names == "no"


def test_load_returns_defaults_when_no_toml(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / "no_such.toml")
    cfg = Config.load()
    assert cfg.track_project_names == "no"


def test_load_reads_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text('[tracking]\ntrack_project_names = "whimsical"\n')
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names == "whimsical"


def test_load_override_wins_over_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text('[tracking]\ntrack_project_names = "yes"\n')
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load(track_project_names="no")
    assert cfg.track_project_names == "no"


def test_load_rejects_invalid_override(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / "no_such.toml")
    with pytest.raises(ValueError, match="whimsical"):
        Config.load(track_project_names="maybe")
    with pytest.raises(ValueError, match="whimsical"):
        Config.load(track_project_names=True)


def test_load_rejects_old_boolean_value(tmp_path, monkeypatch, capsys):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = true\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names == "no"
    assert "Warning" in capsys.readouterr().err


def test_load_rejects_unknown_string_value(tmp_path, monkeypatch, capsys):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text('[tracking]\ntrack_project_names = "maybe"\n')
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names == "no"
    assert "Warning" in capsys.readouterr().err


def test_load_invalid_toml_falls_back_to_defaults(tmp_path, monkeypatch, capsys):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("NOT VALID TOML @@@@")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names == "no"
    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_write_toml_setting_creates_file(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    write_toml_setting("track_project_names", "whimsical")
    assert toml.exists()
    content = toml.read_text()
    assert 'track_project_names = "whimsical"' in content


def test_write_toml_setting_creates_parent_dir_when_missing(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer" / ".tokentracer.toml"
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    assert not toml.parent.exists()
    write_toml_setting("track_project_names", "whimsical")
    assert toml.exists()
    assert 'track_project_names = "whimsical"' in toml.read_text()


def test_write_toml_setting_updates_existing(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text('[tracking]\ntrack_project_names = "no"\n')
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    write_toml_setting("track_project_names", "whimsical")
    content = toml.read_text()
    assert 'track_project_names = "whimsical"' in content
