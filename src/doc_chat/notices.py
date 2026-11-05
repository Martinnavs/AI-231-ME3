"""Finished delegate jobs, pushed by the agent server on a channel of their own (R1).

The channel is a WebSocket to the agent server whose first message is {"notices": true}. The server
then sends {"notice": {"id": int, "text": str}} the moment a job ends, and again after a reconnect
until the client sends {"ack": id}. The client acks when the notice has played to the end or the
user talked over it (it is then not repeated).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass
from typing import Any

import websockets.asyncio.client as ws_client

RECONNECT_S = 2.0
MAX_WORDS = 25


@dataclass
class Notice:
    id: int
    text: str


def notice_line(text: str) -> str:
    """What is said: the first sentence, at most MAX_WORDS words, cut at a word boundary. The whole
    text stays in the job log and in delegate_status."""
    words = " ".join(text.split())
    for end in (". ", "? ", "! "):
        head, sep, _ = words.partition(end)
        if sep:
            words = head + end.strip()
            break
    parts = words.split()
    if len(parts) > MAX_WORDS:
        words = " ".join(parts[:MAX_WORDS]).rstrip(",;:") + "."
    return f"Your earlier request is done: {words}"


class NoticeFeed:
    """The notices waiting to be spoken. `run` keeps the channel open (reconnecting) in the
    background; `take` hands out the oldest; `played` and `cut` ack it (a cut
    notice is not replayed)."""

    def __init__(self, url: str, token: str | None = None) -> None:
        self.url = url
        self.token = token if token is not None else os.environ.get("LAN_SPEECH_TOKEN")
        self.waiting: list[Notice] = []
        self.arrived = asyncio.Event()
        self._ws: Any = None
        self._seen: set[int] = set()

    async def run(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        while True:
            try:
                async with ws_client.connect(self.url, additional_headers=headers) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"notices": True}))
                    async for raw in ws:
                        self._receive(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                self._ws = None
            await asyncio.sleep(RECONNECT_S)

    def _receive(self, raw: str | bytes) -> None:
        try:
            item = json.loads(raw)["notice"]
            nid, text = int(item["id"]), str(item["text"])
        except (ValueError, KeyError, TypeError):
            return
        if nid not in self._seen:  # a reconnect resends what was not acked
            self._seen.add(nid)
            self.waiting.append(Notice(nid, text))
            self.arrived.set()

    def take(self) -> Notice | None:
        if not self.waiting:
            self.arrived.clear()
            return None
        return self.waiting.pop(0)

    async def played(self, notice: Notice) -> None:
        """It played to the end: the server may forget it."""
        await self._ack(notice.id)

    async def cut(self, notice: Notice) -> None:
        """The user talked over it (or said stop): it is not replayed. The server forgets it; the
        job's result stays in delegate_status."""
        await self._ack(notice.id)

    async def _ack(self, nid: int) -> None:
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.send(json.dumps({"ack": nid}))
