from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from src.dashboard import queries

_CONTENT_TYPES = {
    ".html": "text/html", ".js": "application/javascript",
    ".css": "text/css", ".json": "application/json",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}


class _DashboardHandler(BaseHTTPRequestHandler):
    db_path: Path
    static_dir: Path

    def log_message(self, format: str, *args) -> None:
        pass  # daemon mode redirects stdout/stderr to dashboard.log; keep it quiet

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed.path, qs)
        else:
            self._handle_static(parsed.path)

    def _handle_api(self, path: str, qs: dict) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            data = self._dispatch(conn, path, qs)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception:
            self._json(500, {"error": "internal error"})
            return
        finally:
            conn.close()
        if data is None:
            self._json(404, {"error": "not found"})
        else:
            self._json(200, data)

    def _dispatch(self, conn: sqlite3.Connection, path: str, qs: dict) -> dict | list | None:
        if path == "/api/summary":
            return queries.summary(
                conn, qs.get("period", "day"), qs.get("start"), qs.get("end"),
                qs.get("project"), qs.get("source"),
            )
        if path == "/api/heatmap":
            return queries.heatmap(conn, int(qs.get("days", 180)))
        if path == "/api/trend":
            return queries.trend(conn, int(qs.get("days", 30)))
        if path == "/api/projects":
            return queries.projects(conn, qs.get("period", "all"), qs.get("start"), qs.get("end"))
        if path == "/api/projects/detail":
            project = qs.get("project")
            if not project:
                raise ValueError("project query param is required")
            return queries.project_detail(
                conn, project, qs.get("period", "all"), qs.get("start"), qs.get("end"),
            )
        if path == "/api/sync-status":
            return queries.sync_status(conn)
        if path == "/api/meta":
            return queries.meta(conn)
        return None

    def _handle_static(self, path: str) -> None:
        rel = unquote(path.lstrip("/")) or "index.html"
        file_path = (self.static_dir / rel).resolve()
        static_root = self.static_dir.resolve()
        if static_root not in file_path.parents and file_path != static_root:
            self._json(403, {"error": "forbidden"})
            return
        if not file_path.exists() or file_path.is_dir():
            file_path = static_root / "index.html"
        if not file_path.exists():
            self._json(404, {"error": "not found"})
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(file_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(db_path: Path, static_dir: Path, port: int) -> ThreadingHTTPServer:
    handler_cls = type("BoundDashboardHandler", (_DashboardHandler,),
                        {"db_path": db_path, "static_dir": static_dir})
    return ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
