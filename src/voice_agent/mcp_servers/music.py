"""Music MCP server (runs on the Pi as a stdio child of the agent server; ticket set Y3).

One tool, `music(action, query)`, with the actions play, pause, resume, stop, next, volume_up and
volume_down. It keeps what is playing (the search results, which one, paused or not, the level),
asks the laptop's resolver for stream URLs (`GET /search?q=...` with the session token), and
answers with a sentence for the user and an `event` for the client: the laptop's doc_chat decodes
the stream and plays it on the Pi's speaker (it has the microphone and the echo canceller).

    python -m voice_agent.mcp_servers.music --resolver-url http://<laptop cable address>:8796

The token is read from $LAN_SPEECH_TOKEN or ~/.config/lan_speech_token. When nothing is playing,
pause / stop / next / volume answer {"inactive": true} so the caller keeps its old behaviour.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

CONFIG = Path(__file__).resolve().parents[3] / "config" / "music.json"
TOKEN_FILE = Path.home() / ".config" / "lan_speech_token"
ACTIONS = ("play", "pause", "resume", "stop", "next", "volume_up", "volume_down")

Search = Callable[[str], list[dict[str, Any]]]


def short_title(title: str, words: int = 7, chars: int = 50) -> str:
    """ "Taylor Swift - Guilty as Sin (Official Lyric Video)" -> "Taylor Swift - Guilty as Sin"."""
    text = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", title)  # (Official Video), [HD]
    text = re.split(r"\s+[|/]\s+", text)[0]
    text = re.sub(r"[^\w\s&',.!?-]", "", text)  # emoji and decoration
    parts = " ".join(text.split()).split()
    cut = " ".join(parts[:words])
    if len(cut) > chars:
        cut = cut[:chars].rsplit(" ", 1)[0]
    return cut.rstrip(" -,.") or "your music"


def resolver_search(url: str, token: str) -> Search:
    def search(query: str) -> list[dict[str, Any]]:
        reply = httpx.get(
            f"{url.rstrip('/')}/search",
            params={"q": query},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        reply.raise_for_status()
        results: list[dict[str, Any]] = reply.json()["results"]
        return results

    return search


class MusicControl:
    """The state machine behind the tool; `search` is replaced in tests."""

    def __init__(self, search: Search, config: dict[str, Any]) -> None:
        self.search = search
        self.default_query = str(config["default_query"])
        self.level_db = float(config["level_db"])
        self.step_db = float(config["step_db"])
        self.min_db = float(config["min_db"])
        self.results: list[dict[str, Any]] = []
        self.index = 0
        self.state = "idle"  # idle | playing | paused

    @staticmethod
    def _done(
        sentence: str, event: dict[str, Any] | None = None, ok: bool = True
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": ok, "sentence": sentence}
        if event is not None:
            out["event"] = {"event": "music", **event}
        return out

    @staticmethod
    def _inactive() -> dict[str, Any]:
        return {"ok": False, "inactive": True, "sentence": ""}

    def _start(self, hit: dict[str, Any]) -> dict[str, Any]:
        self.state = "playing"
        title = short_title(hit["title"])
        return self._done(
            f"Playing {title}.",
            {"action": "play", "url": hit["stream_url"], "title": title, "db": self.level_db},
        )

    def act(self, action: str, query: str = "") -> dict[str, Any]:
        if action == "play":
            if not query.strip() and self.state == "paused":
                return self.act("resume")
            return self._play(query.strip() or self.default_query)
        if action == "resume":
            if self.state == "paused":
                self.state = "playing"
                return self._done("Resuming.", {"action": "resume"})
            return self._play(self.default_query) if self.state == "idle" else self._inactive()
        if self.state == "idle":
            return self._inactive()
        if action == "pause":
            if self.state == "paused":
                return self._done("It is already paused.")
            self.state = "paused"
            return self._done("Paused.", {"action": "pause"})
        if action == "stop":
            self.state = "idle"
            return self._done("Music stopped.", {"action": "stop"})
        if action == "next":
            if self.index + 1 >= len(self.results):
                return self._done("That was the last result for that search.", ok=False)
            self.index += 1
            return self._start(self.results[self.index])
        if action in ("volume_up", "volume_down"):
            step = self.step_db if action == "volume_up" else -self.step_db
            new = max(self.min_db, min(0.0, self.level_db + step))
            if new == self.level_db:
                return self._done(
                    "That is as loud as the music goes."
                    if action == "volume_up"
                    else "That is as quiet as it goes."
                )
            self.level_db = new
            sentence = "Louder." if action == "volume_up" else "Quieter."
            return self._done(sentence, {"action": "volume", "db": new})
        return self._done("I don't know that music command.", ok=False)

    def _play(self, query: str) -> dict[str, Any]:
        try:
            results = self.search(query)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                return self._done("I can only search for music by name.", ok=False)
            return self._done("The music search isn't working right now.", ok=False)
        except (httpx.HTTPError, ValueError, KeyError):
            return self._done("I can't reach the music search right now.", ok=False)
        if not results:
            return self._done("I couldn't find any music for that.", ok=False)
        self.results, self.index = results, 0
        return self._start(results[0])


def build_server(control: MusicControl) -> FastMCP:
    server = FastMCP("music")

    @server.tool(
        description=(
            "Play music on the speaker. action is play (with a query: an artist, song, genre or "
            "mood), pause, resume, stop, next, volume_up or volume_down."
        )
    )
    def music(action: str, query: str = "") -> str:
        return json.dumps(control.act(action.strip().lower(), query))

    return server


def _token() -> str:
    token = os.environ.get("LAN_SPEECH_TOKEN")
    if token:
        return token
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--resolver-url", required=True)
    args = ap.parse_args(argv)
    config = json.loads(CONFIG.read_text())
    build_server(MusicControl(resolver_search(args.resolver_url, _token()), config)).run(
        show_banner=False
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
