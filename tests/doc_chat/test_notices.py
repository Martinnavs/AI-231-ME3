"""R1: the client side of the notice channel."""

import asyncio
import json
from typing import Any

from websockets.asyncio.server import serve

from doc_chat.notices import Notice, NoticeFeed, notice_line


def test_the_line_and_its_brief_form() -> None:
    text = "The 13th prime from the script is 41. I ran primes.py and read the thirteenth line."
    assert notice_line(text) == f"Your earlier request is done: {text}"
    assert notice_line(text, brief=True) == "Earlier request: The 13th prime from the script is 41."
    long = "word " * 40
    assert len(notice_line(long, brief=True).split()) <= 14 + 2


async def test_the_feed_connects_with_the_token_takes_notices_once_and_acks() -> None:
    received: list[Any] = []
    headers: list[str] = []

    async def handler(ws: Any) -> None:
        headers.append(ws.request.headers.get("Authorization", ""))
        received.append(json.loads(await ws.recv()))
        for _ in range(2):  # the same notice twice, as after a reconnect
            await ws.send(json.dumps({"notice": {"id": 5, "text": "Done."}}))
        received.append(json.loads(await ws.recv()))

    server = await serve(handler, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(server.sockets)).getsockname()[1]}"
    feed = NoticeFeed(url, token="secret")
    task = asyncio.create_task(feed.run())
    await asyncio.wait_for(feed.arrived.wait(), 3)
    await asyncio.sleep(0.1)
    notice = feed.take()
    assert notice is not None and (notice.id, notice.text) == (5, "Done.")
    assert feed.take() is None  # the repeat was ignored
    await feed.played(notice)
    await asyncio.sleep(0.2)
    task.cancel()
    assert headers == ["Bearer secret"]
    assert received == [{"notices": True}, {"ack": 5}]
    server.close()
    await server.wait_closed()


async def test_a_cut_notice_goes_back_once_then_is_acked_and_dropped() -> None:
    acked: list[int] = []

    class Feed(NoticeFeed):
        async def _ack(self, nid: int) -> None:
            acked.append(nid)

    feed = Feed("ws://unused", "t")
    notice = Notice(9, "Done.")
    await feed.cut(notice)
    assert feed.waiting == [notice] and acked == []
    again = feed.take()
    assert again is notice
    await feed.cut(notice)
    assert feed.waiting == [] and acked == [9]
