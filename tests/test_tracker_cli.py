from __future__ import annotations

import pytest

import tracker
from src.commands import collect as collect_cmd
from src.config import Config, Paths


def test_parser_accepts_project_mode():
    parser = tracker.build_parser()
    args = parser.parse_args(["collect", "--project-mode", "whimsical"])
    assert args.project_mode == "whimsical"


def test_parser_rejects_invalid_project_mode():
    parser = tracker.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--project-mode", "true"])


def test_parser_has_no_track_projects_flags():
    parser = tracker.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--track-projects"])


def test_config_set_accepts_valid_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / ".tokentracer.toml")
    parser = tracker.build_parser()
    args = parser.parse_args(["config", "set", "track_project_names", "whimsical"])
    assert args.run(args) == 0
    assert 'track_project_names = "whimsical"' in (
        tmp_path / ".tokentracer.toml"
    ).read_text()


def test_config_without_subcommand_returns_error():
    parser = tracker.build_parser()
    args = parser.parse_args(["config"])
    assert args.run(args) == 1


def _cfg(tmp_path, mode: str) -> Config:
    return Config(
        paths=Paths(
            copilot_home=tmp_path / "copilot",
            claude_projects=tmp_path / "claude",
        ),
        db_path=tmp_path / "usage.db",
        track_project_names=mode,
    )


def test_build_pipeline_returns_identity_store_for_masked_modes(tmp_path):
    _, store = collect_cmd._build_pipeline(_cfg(tmp_path, "whimsical"))
    assert store is not None
    store.close()


def test_build_pipeline_returns_no_identity_store_for_yes_mode(tmp_path):
    _, store = collect_cmd._build_pipeline(_cfg(tmp_path, "yes"))
    assert store is None


def test_cmd_collect_closes_identity_store(tmp_path, monkeypatch):
    closed = []

    class SpyStore:
        def close(self):
            closed.append(True)

    class FakeResult:
        errors = ()
        stores_failed = ()
        records_written = 0
        collectors_run = 0

    class FakePipeline:
        def since(self, _):
            return self

        def stores(self, *_):
            return self

        def run(self):
            return FakeResult()

    monkeypatch.setattr(Config, "load", classmethod(lambda cls, **kw: _cfg(tmp_path, "no")))
    monkeypatch.setattr(collect_cmd, "_build_pipeline", lambda cfg: (FakePipeline(), SpyStore()))
    monkeypatch.setattr(collect_cmd, "_build_stores", lambda cfg: [])

    parser = tracker.build_parser()
    args = parser.parse_args(["collect"])
    assert args.run(args) == 0
    assert closed == [True]


def test_config_set_rejects_boolean_value(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / ".tokentracer.toml")
    parser = tracker.build_parser()
    args = parser.parse_args(["config", "set", "track_project_names", "true"])
    assert args.run(args) == 1
    assert "whimsical" in capsys.readouterr().err


def test_parser_accepts_projects_command():
    parser = tracker.build_parser()
    args = parser.parse_args(["projects"])
    assert args.cmd == "projects"


def test_cmd_projects_lists_identities(tmp_path, capsys):
    from src.project_identity import ProjectIdentityStore

    db = tmp_path / "usage.db"
    store = ProjectIdentityStore(db)
    name = store.resolve_whimsical("C:/Work/MyProj")
    store.close()

    parser = tracker.build_parser()
    args = parser.parse_args(["--db", str(db), "projects"])
    assert args.run(args) == 0
    out = capsys.readouterr().out
    assert "c:/work/myproj" in out
    assert name in out


def test_cmd_projects_empty_db(tmp_path, capsys):
    parser = tracker.build_parser()
    args = parser.parse_args(["--db", str(tmp_path / "usage.db"), "projects"])
    assert args.run(args) == 0
    assert "No project identities" in capsys.readouterr().out
