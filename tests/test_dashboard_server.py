from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from src.dashboard.server import make_server
from src.models import SessionRecord
from src.stores.sqlite import SqliteStore


@pytest.fixture
def running_server(tmp_db, tmp_path):
    store = SqliteStore(tmp_db)
    today = date.today().isoformat()
    store.upsert([
        SessionRecord(session_id="s1", source="claude_cli", model="claude-sonnet-4-6",
                      date=today, input_tokens=100, output_tokens=20, project="proj-a"),
    ])
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")

    server = make_server(tmp_db, static_dir, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=2)


def _get(port: int, path: str) -> tuple[int, dict | str]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url) as resp:
            body = resp.read().decode("utf-8")
            ct = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(body) if "json" in ct else body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


def test_summary_endpoint(running_server):
    status, body = _get(running_server, "/api/summary?period=all")
    assert status == 200
    assert body["total_tokens"] == 120


def test_summary_endpoint_invalid_period_returns_400(running_server):
    status, body = _get(running_server, "/api/summary?period=bogus")
    assert status == 400
    assert "error" in body


def test_heatmap_endpoint(running_server):
    status, body = _get(running_server, "/api/heatmap?days=30")
    assert status == 200
    assert isinstance(body, list)


def test_project_detail_endpoint(running_server):
    status, body = _get(running_server, "/api/projects/detail?project=proj-a&period=all")
    assert status == 200
    assert body["total_tokens"] == 120


def test_sync_status_endpoint(running_server):
    status, body = _get(running_server, "/api/sync-status")
    assert status == 200
    assert body == {"last_collected_at": None, "stores": []}


def test_meta_endpoint(running_server):
    status, body = _get(running_server, "/api/meta")
    assert status == 200
    assert "most_recent_data_at" in body


def test_unknown_api_route_returns_404(running_server):
    status, body = _get(running_server, "/api/nope")
    assert status == 404


def test_static_index_served(running_server):
    status, body = _get(running_server, "/")
    assert status == 200
    assert "dashboard" in body


def test_static_path_traversal_blocked(running_server):
    status, _ = _get(running_server, "/../../etc/passwd")
    assert status in (403, 404)
