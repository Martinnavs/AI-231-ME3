"""R1 and R3: a finished job is spoken when idle; every line of ours can be talked over."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from test_bargein import Interruption, session, turn  # noqa: E402

import doc_chat.playback as playback  # noqa: E402
from doc_chat.fillers import Fillers  # noqa: E402
from doc_chat.notices import Notice, NoticeFeed  # noqa: E402

CONFIG = {
    "timing": {"stall_s": 0.02, "notice_s": 0.05, "giveup_s": 0.1},
    "stall": ["Mm, one moment."],
    "notice": ["Sorry, this is taking a little longer than usual."],
    "giveup": ["Sorry, that's taking too long. Could you ask me again?"],
    "default": ["One moment."],
    "tools": {},
}


class Player:
    def __init__(self, play_s: float = 5.0) -> None:
        self.pushed: list[bytes] = []
        self.aborted = False
        self.closed = False
        self.play_s = play_s

    def push(self, pcm: bytes) -> None:
        self.pushed.append(pcm)

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:  # waits for the audio to play out, unless aborted
        end = time.monotonic() + self.play_s
        while time.monotonic() < end and not self.aborted:
            time.sleep(0.005)
        self.closed = True


@pytest.fixture
def player(monkeypatch: pytest.MonkeyPatch) -> Player:
    made = Player()
    monkeypatch.setattr(playback, "make_player", lambda *a, **k: made)
    return made


def _playing_session() -> tuple[Any, Any]:
    s, client = session()
    s.args.play = True
    s.fillers = Fillers(CONFIG)
    s.fillers.audio = {line: line.encode() for line in s.fillers.lines()}

    async def speak_text(text: str, voice: Any = None, on_chunk: Any = None, **_: Any) -> Any:
        on_chunk(b"\x00\x00" * 100)

    client.speak_text = speak_text
    client.last_voiced = 0.0
    client.canceller = None
    return s, client


async def test_the_give_up_line_can_be_talked_over(player: Player) -> None:
    s, client = _playing_session()

    async def handle(idx: int, heard: Any) -> None:
        await asyncio.sleep(10)  # nothing ever comes back

    s.handle = handle  # type: ignore[method-assign]

    async def talk_during_the_line() -> None:
        await asyncio.sleep(0.5)  # the give-up (0.1 s) has started and its line is playing
        client.hub.put(0.4, 0.4, 0.4, 0.4, 0.4)

    asyncio.create_task(talk_during_the_line())
    result = await asyncio.wait_for(s.respond(1, turn("will it rain")), timeout=5)
    assert isinstance(result, Interruption) and result.kind == "barge_in"
    assert player.aborted and client.tts_cancels >= 1
    assert s.records[-1]["gave_up"] is True


async def test_a_wait_line_that_was_talked_over_is_a_barge_in_like_an_answer(
    player: Player,
) -> None:
    s, client = session()

    async def handle(idx: int, heard: Any) -> None:
        s._filler_started = time.perf_counter()  # "Mm, one moment." has started playing
        await asyncio.sleep(10)

    s.handle = handle  # type: ignore[method-assign]

    async def talk() -> None:
        await asyncio.sleep(0.1)
        client.hub.put(0.4, 0.4, 0.4, 0.4, 0.4)

    asyncio.create_task(talk())
    result = await asyncio.wait_for(s.respond(1, turn("tell me about volcanoes")), timeout=5)
    assert result is not None and result.kind == "barge_in"  # was "resumed": the line was ignored
    assert s.records[-1]["interrupted"] == "barge_in"


async def test_cutting_a_line_off_is_fast(player: Player) -> None:
    s, client = _playing_session()
    client.hub.put(0.4, 0.4, 0.4, 0.4, 0.4)
    t0 = time.perf_counter()
    cut = await asyncio.wait_for(s._speak_line("Still working on it."), timeout=5)
    assert cut is not None and cut.cancel_ms < 50 and player.aborted
    assert time.perf_counter() - t0 < 2


async def test_a_line_nobody_talks_over_plays_to_the_end(player: Player) -> None:
    s, _client = _playing_session()
    player.play_s = 0.05
    assert await s._speak_line("Fine.", b"\x00\x00" * 10) is None
    assert player.closed and not player.aborted and player.pushed == [b"\x00\x00" * 10]


class Feed(NoticeFeed):
    def __init__(self) -> None:
        super().__init__("ws://unused", "t")
        self.acked: list[int] = []

    async def _ack(self, nid: int) -> None:
        self.acked.append(nid)


async def test_a_finished_job_is_spoken_when_idle_and_acked_after_it_played(
    player: Player,
) -> None:
    s, client = _playing_session()
    player.play_s = 0.05
    feed = s.notice_feed = Feed()
    spoken: list[str] = []
    inner = s._speak_line

    async def speak(text: str, pcm: bytes | None = None) -> Any:
        spoken.append(text)
        return await inner(text, pcm)

    s._speak_line = speak  # type: ignore[method-assign]
    heard_at: list[float] = []

    async def listen(**kw: Any) -> Any:
        await asyncio.sleep(0.6)  # nobody speaks for a while
        heard_at.append(time.perf_counter())
        return turn("what time is it")

    client.listen_turn = listen
    feed.waiting.append(Notice(7, "The 13th prime is 41."))
    t0 = time.perf_counter()
    heard, over = await s.hear_idle()
    assert spoken == ["Your earlier request is done: The 13th prime is 41."]
    assert feed.acked == [7] and heard.text == "what time is it" and over is False
    assert heard_at[0] - t0 >= 0.6  # listening went on after the notice


async def test_a_notice_waits_while_the_user_is_speaking(player: Player) -> None:
    s, client = _playing_session()
    feed = s.notice_feed = Feed()
    spoken: list[str] = []

    async def speak(text: str, pcm: bytes | None = None) -> Any:
        spoken.append(text)

    s._speak_line = speak  # type: ignore[method-assign]

    async def listen(**kw: Any) -> Any:
        for _ in range(8):
            client.last_voiced = time.perf_counter()  # a voiced block just now
            await asyncio.sleep(0.1)
        return turn("play some music")

    client.listen_turn = listen
    feed.waiting.append(Notice(1, "Done."))
    heard, _ = await s.hear_idle()
    assert heard.text == "play some music" and spoken == [] and feed.waiting  # still waiting


async def test_a_notice_cut_by_barge_in_comes_back_once_and_briefly(player: Player) -> None:
    s, client = _playing_session()
    feed = s.notice_feed = Feed()
    spoken: list[str] = []
    cuts = [True, True]  # both times the user talks over it

    async def speak(text: str, pcm: bytes | None = None) -> Any:
        spoken.append(text)
        return Interruption("barge_in", [], 1.0, 0.0) if cuts.pop(0) else None

    s._speak_line = speak  # type: ignore[method-assign]
    text = "The 13th prime from the script is 41. I ran primes.py and read the thirteenth line."
    feed.waiting.append(Notice(3, text))
    calls = [0]

    async def listen(**kw: Any) -> Any:
        calls[0] += 1
        if kw.get("audio") is None:  # idle listening: nobody speaks yet
            await asyncio.sleep(10)
        return turn("hold on")  # the user talking over the notice

    client.listen_turn = listen
    # first delivery: cut by the user, who is then heard (barge-in)
    heard, over = await s.hear_idle()
    assert over is True and heard.text == "hold on" and feed.acked == []
    assert [n.id for n in feed.waiting] == [3]  # back in the queue
    # next idle moment: the brief form, cut again -> given up, acked, never again
    heard, over = await s.hear_idle()
    assert spoken[0].startswith("Your earlier request is done:")
    assert spoken[1] == "Earlier request: The 13th prime from the script is 41."
    assert feed.acked == [3] and feed.waiting == [] and over is True
    assert len(spoken) == 2
