"""Local-only standard-library API; credentials never enter browser responses."""
from __future__ import annotations

import io
import json
import re
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .contracts import WorkflowError, draft_of
from .runtime import RunManager, report_markdown, sanitize_import

ASSETS = Path(__file__).parent / "static"
LIMIT = 600000


def make_server(config, host="127.0.0.1", port=8010, root=None):
    manager = RunManager(config, root or config.workspace / ".workflow_runs")
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass

        def _valid_host(self):
            try:
                hostname = urlparse("http://" + self.headers.get("Host", "")).hostname
                return hostname in ("127.0.0.1", "localhost")
            except ValueError:
                return False

        def _send(self, body, content_type="application/json; charset=utf-8", status=200, filename=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self._valid_host():
                return self._send({"error": "只接受本机访问。"}, status=403)
            path = urlparse(self.path).path
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/static/app.js": ("app.js", "text/javascript; charset=utf-8"), "/static/style.css": ("style.css", "text/css; charset=utf-8"), "/static/favicon.svg": ("favicon.svg", "image/svg+xml")}
            if path in assets:
                name, mime = assets[path]
                return self._send((ASSETS / name).read_bytes(), mime)
            if path == "/api/status":
                return self._send({"application": "CodeFlow", "configured": bool(config.api_key), "model": config.model, "active_run": manager.active})
            if path == "/api/runs":
                return self._send({"runs": manager.store.list()})
            match = re.fullmatch(r"/api/runs/([0-9a-f]{32})(?:/(export\.json|report\.md|bundle\.zip))?", path)
            if not match:
                return self._send({"error": "未找到资源。"}, status=404)
            try:
                run = manager.snapshot(match[1])
                export = match[2]
                if export == "report.md":
                    return self._send(report_markdown(run), "text/markdown; charset=utf-8", filename="codeflow-report.md")
                if export == "export.json":
                    return self._send(run, filename="codeflow-run.json")
                if export == "bundle.zip":
                    draft = draft_of(run)
                    if not draft:
                        raise WorkflowError("尚未生成代码文件。")
                    stream = io.BytesIO()
                    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                        for file in draft["files"]:
                            archive.writestr(file["path"], file["content"])
                        archive.writestr("CODEFLOW_REPORT.md", report_markdown(run))
                    return self._send(stream.getvalue(), "application/zip", filename="codeflow-delivery.zip")
                return self._send(run)
            except KeyError:
                return self._send({"error": "运行记录不存在。"}, status=404)
            except WorkflowError as exc:
                return self._send({"error": str(exc)}, status=400)

        def do_POST(self):
            if not self._valid_host() or self.headers.get("Origin") not in (None, "http://" + self.headers.get("Host", "")):
                self.close_connection = True
                return self._send({"error": "不接受跨站请求。"}, status=403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= LIMIT:
                    self.close_connection = True
                    return self._send({"error": "请求为空或超过 600 KB。"}, status=413)
                if self.headers.get_content_type() != "application/json":
                    return self._send({"error": "请使用 JSON 请求。"}, status=415)
                self.connection.settimeout(15)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise WorkflowError("请求需要是 JSON 对象。")
                path = urlparse(self.path).path
                if path == "/api/runs":
                    return self._send(manager.create(payload), status=201)
                if path == "/api/runs/import":
                    return self._send(manager.store.add(sanitize_import(payload)), status=201)
                match = re.fullmatch(r"/api/runs/([0-9a-f]{32})/(pause|resume)", path)
                if match:
                    return self._send(manager.pause(match[1]) if match[2] == "pause" else manager.start(match[1]))
                return self._send({"error": "未找到操作。"}, status=404)
            except KeyError:
                return self._send({"error": "运行记录不存在。"}, status=404)
            except (WorkflowError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._send({"error": str(exc)[:1000]}, status=400)
            except OSError:
                return self._send({"error": "本机存储或网络读取失败，请检查目录权限和磁盘空间。"}, status=500)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.manager = manager
    return server
