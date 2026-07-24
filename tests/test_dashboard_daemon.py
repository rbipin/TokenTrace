from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from src.dashboard import daemon


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def _fail(*args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="boom")


def test_resolve_executable_prefers_path(monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "/usr/local/bin/tokentracer")
    assert daemon.resolve_executable() == ["/usr/local/bin/tokentracer"]


def test_resolve_executable_falls_back_to_python(monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda name: None)
    argv = daemon.resolve_executable()
    assert argv[0] == daemon.sys.executable
    assert argv[1].endswith("tracker.py")


def test_install_macos_writes_plist_and_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(daemon.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "/usr/local/bin/tokentracer")
    calls = []
    with patch.object(daemon.subprocess, "run", side_effect=lambda *a, **k: (calls.append(a), _ok())[1]):
        daemon.install(8420)
    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.ai-token-tracer.dashboard.plist"
    assert plist_path.exists()
    content = plist_path.read_text()
    assert "8420" in content
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content
    assert calls[0][0][:2] == ["launchctl", "unload"]
    assert calls[1][0][:2] == ["launchctl", "load"]


def test_install_macos_raises_on_load_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(daemon.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "/usr/local/bin/tokentracer")
    with patch.object(daemon.subprocess, "run", side_effect=[_ok(), _fail()]):
        with pytest.raises(RuntimeError, match="launchctl load failed"):
            daemon.install(8420)


def test_uninstall_macos_noop_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(daemon.Path, "home", lambda: tmp_path)
    daemon.uninstall()  # should not raise


def test_install_windows_creates_task(monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Windows")
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "C:\\tools\\tokentracer.exe")
    with patch.object(daemon.subprocess, "run", return_value=_ok()) as mock_run:
        daemon.install(8420)
    args = mock_run.call_args[0][0]
    assert args[:3] == ["schtasks", "/Create", "/F"]
    assert "ai-token-tracer-dashboard" in args


def test_install_unsupported_os_raises(monkeypatch):
    monkeypatch.setattr(daemon.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="unsupported OS"):
        daemon.install(8420)
