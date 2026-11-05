"""Finished delegate jobs, pushed by the agent server on a channel of their own (R1).

The channel is a WebSocket to the agent server whose first message is {"notices": true}. The server
then sends {"notice": {"id": int, "text": str}} the moment a job ends, and again after a reconnect
until the client sends {"ack": id}. The client acks only after the notice has played to the end.
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
BRIEF_WORDS = 14


@dataclass
class Notice:
    id: int
    text: str
    cuts: int = 0  # times a barge-in cut it off


def notice_line(text: str, brief: bool = False) -> str:
    """What is said. The second try, after a barge-in cut the first, is the first sentence only."""
    text = " ".join(text.split())
    if not brief:
        return f"Your earlier request is done: {text}"
    first = text.split(". ")[0].rstrip(".")
    words = first.split()
    if len(words) > BRIEF_WORDS:
        first = " ".join(words[:BRIEF_WORDS])
    return f"Earlier request: {first}."


class NoticeFeed:
    """The notices waiting to be spoken. `run` keeps the channel open (reconnecting) in the
    background; `take` hands out the oldest; `played` acks it, `cut` puts it back once."""

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
        """A barge-in cut it off. The first time it goes back (it comes back briefly at the next
        idle moment); the second time it is given up and acked, so it never comes back again."""
        notice.cuts += 1
        if notice.cuts == 1:
            self.waiting.insert(0, notice)
            self.arrived.set()
        else:
            await self._ack(notice.id)

    async def _ack(self, nid: int) -> None:
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.send(json.dumps({"ack": nid}))
