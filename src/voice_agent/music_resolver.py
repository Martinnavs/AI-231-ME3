"""The music search, on the laptop: a small HTTP service that turns a query into stream URLs.

The Pi's music MCP server calls it (GET /search?q=...) with the session token. It runs yt-dlp's
`ytsearch5:<query>` (audio only, best format, no cookies, no login, no arbitrary URLs: only a
search phrase of up to 100 characters), skips results longer than `max_minutes` unless the query
asks for a mix or something long, and caches a query's answer for 10 minutes. Nothing here plays
audio; the laptop's doc_chat decodes the returned stream URL with ffmpeg.

Using yt-dlp this way is against YouTube's terms of service: personal use, not for anything
published.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hmac
import json
import os
import re
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

CONFIG = Path(__file__).resolve().parents[2] / "config" / "music.json"
MAX_QUERY = 100
TIMEOUT_S = 8.0
CACHE_S = 600.0
LONG_WORDS = frozenset(
    {"mix", "long", "hour", "hours", "nonstop", "marathon", "compilation", "playlist", "medley"}
)
_URL = re.compile(r"https?:|://|www\.|\.com\b|\.be\b|\.net\b", re.IGNORECASE)

Extract = Callable[[str, int], list[dict[str, Any]]]


def sanitise(query: str) -> str | None:
    """A search phrase (control characters out, spaces collapsed, at most 100 characters), or None
    when it is empty or looks like a URL (the user may only search, never name a address)."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(query))
    text = " ".join(text.split())[:MAX_QUERY].strip()
    if not text or _URL.search(text):
        return None
    return text


def wants_long(query: str) -> bool:
    return bool(set(re.findall(r"[a-z]+", query.lower())) & LONG_WORDS)


def ytdlp_extract(query: str, count: int) -> list[dict[str, Any]]:
    """The entries of `ytsearch<count>:<query>` (no download, no cookies)."""
    import yt_dlp  # type: ignore[import-untyped]

    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "noplaylist": True,
        "socket_timeout": TIMEOUT_S,
        "skip_download": True,
        "cookiefile": None,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
    return [e for e in (info or {}).get("entries") or [] if e]


class Resolver:
    def __init__(
        self,
        extract: Extract = ytdlp_extract,
        *,
        max_minutes: float = 15,
        results: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.extract, self.max_minutes, self.results, self.clock = (
            extract,
            max_minutes,
            results,
            clock,
        )
        self._cache: dict[tuple[str, bool], tuple[float, list[dict[str, Any]]]] = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def search(self, query: str) -> list[dict[str, Any]]:
        """Up to `results` {title, duration, stream_url, id}, best first. Raises ValueError for a
        query that is not a search phrase, TimeoutError after 8 s."""
        phrase = sanitise(query)
        if phrase is None:
            raise ValueError("not a search phrase")
        long_ok = wants_long(phrase)
        key = (phrase.lower(), long_ok)
        hit = self._cache.get(key)
        if hit is not None and self.clock() - hit[0] < CACHE_S:
            return hit[1]
        future = self._pool.submit(self.extract, phrase, self.results)
        try:
            entries = future.result(timeout=TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("the search took too long") from None
        out: list[dict[str, Any]] = []
        for e in entries:
            url, duration = e.get("url"), e.get("duration")
            if not url or not str(url).startswith("https://"):
                continue
            if not long_ok and (duration is None or duration > self.max_minutes * 60):
                continue
            out.append(
                {
                    "title": str(e.get("title") or "")[:200],
                    "duration": duration,
                    "stream_url": str(url),
                    "id": str(e.get("id") or ""),
                }
            )
        self._cache[key] = (self.clock(), out)
        return out


def make_server(resolver: Resolver, host: str, port: int, token: str) -> ThreadingHTTPServer:
    """One bound address (never every interface), the bearer token on every request."""
    if host in ("0.0.0.0", "", "::"):  # noqa: S104 - refused, not bound
        raise ValueError("bind one address, not every interface")
    secret = token.encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "music"
        sys_version = ""

        def _json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            given = (self.headers.get("Authorization") or "").removeprefix("Bearer ").encode()
            if not hmac.compare_digest(given, secret):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            url = urlsplit(self.path)
            if url.path != "/search":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            query = (parse_qs(url.query).get("q") or [""])[0]
            try:
                self._json(HTTPStatus.OK, {"results": resolver.search(query)})
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "not a search phrase"})
            except TimeoutError:
                self._json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "timeout"})
            except Exception as exc:  # yt-dlp has many ways to fail; the Pi gets one answer
                self._json(HTTPStatus.BAD_GATEWAY, {"error": type(exc).__name__})

        def log_message(self, format: str, *args: object) -> None:
            return  # the query is the user's; not logged

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", required=True, help="the laptop's cable address (config/pi.env)")
    ap.add_argument("--port", type=int, default=8796)
    args = ap.parse_args(argv)
    token = os.environ.get("LAN_SPEECH_TOKEN")
    if not token:
        print("LAN_SPEECH_TOKEN is not set; refusing to serve without a token")
        return 2
    cfg = json.loads(CONFIG.read_text())
    resolver = Resolver(max_minutes=cfg["max_minutes"], results=cfg["results"])
    server = make_server(resolver, args.host, args.port, token)
    print(f"music resolver on http://{args.host}:{args.port}/search", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
