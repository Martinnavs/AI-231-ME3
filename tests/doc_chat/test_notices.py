"""R1: the client side of the notice channel."""

import asyncio
import json
from typing import Any

from websockets.asyncio.server import serve

from doc_chat.notices import MAX_WORDS, Notice, NoticeFeed, notice_line


def test_a_spoken_notice_is_the_first_sentence_cut_at_a_word_boundary() -> None:
    text = "The 13th prime from the script is 41. I ran primes.py and read the thirteenth line."
    assert (
        notice_line(text) == "Your earlier request is done: The 13th prime from the script is 41."
    )
    long = " ".join(f"word{i}" for i in range(70)) + ". Second sentence."
    said = notice_line(long).removeprefix("Your earlier request is done: ")
    assert said.split()[-1] == "word24." and len(said.split()) == MAX_WORDS
    assert "Second" not in said
    flappy = (
        "I wrote flappy_bird.py, a complete Flappy Bird clone with pygame: the bird flaps with the "
        "space bar, pipes scroll left with random gaps, the score counts passed pipes, and it "
        "restarts on a collision. I tested it headless for 600 frames."
    )
    assert len(notice_line(flappy).split()) <= MAX_WORDS + 5  # the prefix is five words
    assert notice_line("Short answer") == "Your earlier request is done: Short answer"


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


async def test_a_cut_notice_is_acked_at_once_and_never_comes_back() -> None:
    acked: list[int] = []

    class Feed(NoticeFeed):
        async def _ack(self, nid: int) -> None:
            acked.append(nid)

    feed = Feed("ws://unused", "t")
    await feed.cut(Notice(9, "Done."))
    assert feed.waiting == [] and acked == [9]


def test_a_notice_that_repeats_what_was_just_said_becomes_a_short_done() -> None:
    summary = "I changed the Snarky Puppy website to include the international holidays."
    told = ["I am updating the Snarky Puppy website to include the international holidays."]
    assert notice_line(summary, told) == "That's done."
    assert notice_line(summary, ["The weather in Cebu is rain."]).startswith("Your earlier request")
    assert notice_line(summary, []).startswith("Your earlier request is done: I changed")
    assert notice_line("Done.", told).startswith("Your earlier request")  # too little to compare
