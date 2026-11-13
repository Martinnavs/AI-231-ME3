"""HTTP front of the home dashboard (standard library): the page, GET /api/state, POST /api/command.

python -m home_dashboard --host 0.0.0.0 --port 8000 [--phonebook config/phonebook.json]
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .state import AppState, CommandError, load_contacts

STATIC = Path(__file__).parent / "static"
FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/style.css": "style.css",
    "/app.js": "app.js",
}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript"}
MAX_BODY = 4096


def make_handler(state: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def send(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, data: object) -> None:
            self.send(status, json.dumps(data).encode())

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                self.send_json(200, state.snapshot())
            elif path in FILES:
                name = FILES[path]
                self.send(200, (STATIC / name).read_bytes(), TYPES[Path(name).suffix])
            else:
                self.send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/reset":
                state.reset()
                self.send_json(200, {"ok": True, "state": state.snapshot()})
                return
            if path != "/api/command":
                self.send_json(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(min(length, MAX_BODY)))
                intent, slots = str(body["intent"]), dict(body.get("slots") or {})
            except (ValueError, KeyError, TypeError):
                self.send_json(
                    400, {"ok": False, "error": 'body must be {"intent": ..., "slots": {...}}'}
                )
                return
            try:
                self.send_json(200, state.command(intent, slots))
            except CommandError as exc:
                self.send_json(400, {"ok": False, "error": str(exc), "state": state.snapshot()})

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--phonebook",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "pipeline" / "config" / "phonebook.json",
    )
    args = parser.parse_args(argv)
    state = AppState(load_contacts(args.phonebook))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    print(f"home dashboard on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0
