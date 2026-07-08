from __future__ import annotations

import pytest

import tracker


def test_parser_accepts_project_mode():
    parser, _ = tracker._build_parser()
    args = parser.parse_args(["collect", "--project-mode", "whimsical"])
    assert args.project_mode == "whimsical"


def test_parser_rejects_invalid_project_mode():
    parser, _ = tracker._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--project-mode", "true"])


def test_parser_has_no_track_projects_flags():
    parser, _ = tracker._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--track-projects"])


def test_config_set_accepts_valid_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / ".tokentracer.toml")
    parser, _ = tracker._build_parser()
    args = parser.parse_args(["config", "set", "track_project_names", "whimsical"])
    assert tracker.cmd_config_set(args) == 0
    assert 'track_project_names = "whimsical"' in (
        tmp_path / ".tokentracer.toml"
    ).read_text()


def test_config_set_rejects_boolean_value(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / ".tokentracer.toml")
    parser, _ = tracker._build_parser()
    args = parser.parse_args(["config", "set", "track_project_names", "true"])
    assert tracker.cmd_config_set(args) == 1
    assert "whimsical" in capsys.readouterr().err
