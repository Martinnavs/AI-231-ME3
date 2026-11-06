"""Static server for the web apps the delegate writes: http://127.0.0.1:8795/<name>/.

Serves only ~/agent_sandbox/workspace/apps/<name>/ (names [a-z0-9_-]) on one address (127.0.0.1,
or the Pi's cable address in HARNESS=pi, never every interface), read-only, GET and HEAD,
no directory listing, no symlinks, no dotfiles. Agent-written JavaScript runs in the owner's
browser, so every response carries a Content-Security-Policy that stops it from fetching, opening
a WebSocket or loading anything outside its own folder: it cannot reach the other localhost
services or the internet (it can still navigate the tab; no CSP can stop that).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PORT = 8795
ROOT = Path("~/agent_sandbox/workspace/apps").expanduser()
NAME = re.compile(r"[a-z0-9_-]{1,40}")
MAX_BYTES = 5_000_000
CSP = (
    "default-src 'self'; connect-src 'none'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
}


def resolve(root: Path, url_path: str) -> Path | str | None:
    """The file `url_path` names under `root/<name>/`; a redirect target ("/name/") for a bare
    app name; None for anything else (not an app, a hidden file, a traversal, a symlink, a
    directory, a missing file)."""
    parts = unquote(urlsplit(url_path).path).split("/")[1:]
    if not parts or not NAME.fullmatch(parts[0]):
        return None
    name, rest = parts[0], parts[1:]
    if not rest:
        return f"/{name}/"
    if rest == [""]:
        rest = ["index.html"]
    if any(p in ("", ".", "..") or p.startswith(".") or "\\" in p or "\0" in p for p in rest):
        return None
    base = root / name
    if base.is_symlink():
        return None
    target = base
    for (
        part
    ) in rest:  # no symlink on the way: a job could point one at a file outside the workspace
        target = target / part
        if target.is_symlink():
            return None
    try:
        if not target.is_file():
            return None
        if Path(os.path.realpath(base)) not in Path(os.path.realpath(target)).parents:
            return None
    except OSError:
        return None
    return target


class Handler(BaseHTTPRequestHandler):
    root = ROOT
    server_version = "apps"
    sys_version = ""

    def _send(self, status: HTTPStatus, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(status)
        for key, value in HEADERS.items():
            self.send_header(key, value)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve(self) -> None:
        found = resolve(self.root, self.path)
        if isinstance(found, str):
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            for key, value in HEADERS.items():
                self.send_header(key, value)
            self.send_header("Location", found)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if found is None or found.stat().st_size > MAX_BYTES:
            self._send(HTTPStatus.NOT_FOUND, b"not found")
            return
        ctype = TYPES.get(found.suffix.lower(), "application/octet-stream")
        self._send(HTTPStatus.OK, found.read_bytes(), ctype)

    do_GET = do_HEAD = _serve

    def _refuse(self) -> None:
        self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"read only")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {self.address_string()} {format % args}\n")


def make_server(
    root: Path = ROOT, port: int = PORT, host: str = "127.0.0.1"
) -> ThreadingHTTPServer:
    """Bound to one address: 127.0.0.1, or (HARNESS=pi) the Pi's cable address. Never 0.0.0.0."""
    if host in ("0.0.0.0", "", "::"):  # noqa: S104 - refused, not bound
        raise ValueError("bind one address, not every interface")
    handler = type("AppsHandler", (Handler,), {"root": root})
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1", help="the one address to bind")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    args.root.mkdir(parents=True, exist_ok=True)
    server = make_server(args.root, args.port, args.host)
    print(f"apps server on http://{args.host}:{args.port}/<name>/ from {args.root}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
