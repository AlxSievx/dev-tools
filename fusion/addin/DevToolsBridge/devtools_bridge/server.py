"""HTTP endpoints of the bridge. Runs on a background thread; no adsk here.

Every request needs `Authorization: Bearer <token>`. Work is only queued here;
`notify()` tells the add-in shell to wake Fusion's main thread.
"""
import hmac
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from .jobs import JobQueue

MAX_BODY = 10 * 1024 * 1024
JOB_PATH = re.compile(r"^/jobs/([0-9a-f]{8})$")


def make_server(queue: JobQueue, token: str, notify: Callable[[], None],
                status: Callable[[], dict], host: str = "127.0.0.1",
                port: int = 7655) -> ThreadingHTTPServer:

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            sent = self.headers.get("Authorization", "")
            return hmac.compare_digest(sent.encode(), f"Bearer {token}".encode())

        def _body(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return None
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
            except ValueError:
                return None
            return body if isinstance(body, dict) else None

        def _submit(self, kind: str, payload: dict, label: str = ""):
            job = queue.submit(kind, payload, label)
            notify()
            self._send(202, {"id": job.id})

        def _route(self, method: str):
            if not self._authorized():
                return self._send(401, {"error": "Unauthorized: token does not match ~/.fusion-mcp-secret"})
            url = urlsplit(self.path)
            m = JOB_PATH.match(url.path)

            if method == "GET" and url.path == "/health":
                return self._send(200, {"ok": True, **status(), **queue.snapshot()})

            if method == "GET" and m:
                job = queue.get(m.group(1))
                if not job:
                    return self._send(404, {"error": f"job {m.group(1)} is not known"})
                since = parse_qs(url.query).get("since", ["0"])[0]
                return self._send(200, job.to_dict(int(since) if since.isdigit() else 0))

            if method == "DELETE" and m:
                job = queue.get(m.group(1))
                if not job:
                    return self._send(404, {"error": f"job {m.group(1)} is not known"})
                if queue.cancel(job.id):
                    return self._send(200, {"cancelled": True})
                return self._send(409, {"error": f"job {job.id} is {job.state}; only queued jobs can be cancelled"})

            if method == "POST" and url.path in ("/jobs", "/screenshot", "/reset"):
                body = self._body()
                if body is None:
                    return self._send(400, {"error": "body must be a JSON object"})
                label = body.get("label") if isinstance(body.get("label"), str) else ""
                if url.path == "/reset":
                    return self._submit("reset", {}, label)
                if url.path == "/screenshot":
                    views = body.get("views")
                    if not views or not isinstance(views, list) or not all(isinstance(v, str) for v in views):
                        return self._send(400, {"error": "views must be a non-empty list of names"})
                    payload = {"views": views, "width": int(body.get("width") or 1600),
                               "height": int(body.get("height") or 1000), "dir": body.get("dir")}
                    return self._submit("shot", payload, label)
                given = [k for k in ("script", "path") if k in body]
                if len(given) != 1 or not isinstance(body[given[0]], str):
                    return self._send(400, {"error": "give exactly one of script or path, as a string"})
                return self._submit("script", {given[0]: body[given[0]]}, label)

            return self._send(404, {"error": f"no endpoint {method} {url.path}"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
