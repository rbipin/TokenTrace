from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from src.commands.dashboard import DashboardCommand


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    cmd = DashboardCommand()
    cmd.configure(parser)
    return parser.parse_args(argv)


def test_invalid_port_rejected():
    cmd = DashboardCommand()
    args = _parse(["--port", "0"])
    assert cmd.run(args) == 1


def test_stop_calls_daemon_uninstall():
    cmd = DashboardCommand()
    args = _parse(["--stop"])
    with patch("src.commands.dashboard.daemon.uninstall") as mock_uninstall:
        assert cmd.run(args) == 0
    mock_uninstall.assert_called_once()


def test_stop_reports_error(capsys):
    cmd = DashboardCommand()
    args = _parse(["--stop"])
    with patch("src.commands.dashboard.daemon.uninstall", side_effect=RuntimeError("boom")):
        assert cmd.run(args) == 1
    assert "boom" in capsys.readouterr().out


def test_daemon_calls_daemon_install():
    cmd = DashboardCommand()
    args = _parse(["--daemon", "--port", "8421"])
    with patch("src.commands.dashboard.daemon.install") as mock_install:
        assert cmd.run(args) == 0
    mock_install.assert_called_once_with(8421)


def test_foreground_errors_when_frontend_not_built(tmp_path, monkeypatch):
    cmd = DashboardCommand()
    monkeypatch.setattr("src.commands.dashboard._FRONTEND_DIST", tmp_path / "nope")
    args = _parse([])
    assert cmd.run(args) == 1


def test_foreground_starts_and_stops_server(tmp_path, monkeypatch):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html></html>")
    monkeypatch.setattr("src.commands.dashboard._FRONTEND_DIST", frontend)
    fake_server = MagicMock()
    fake_server.serve_forever.side_effect = KeyboardInterrupt
    with patch("src.commands.dashboard.make_server", return_value=fake_server) as mock_make:
        cmd = DashboardCommand()
        args = _parse(["--db", str(tmp_path / "usage.db")])
        assert cmd.run(args) == 0
    mock_make.assert_called_once()
    fake_server.server_close.assert_called_once()
