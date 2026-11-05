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


async def test_a_notice_cut_by_barge_in_is_not_replayed(player: Player) -> None:
    s, client = _playing_session()
    feed = s.notice_feed = Feed()
    spoken: list[str] = []

    async def speak(text: str, pcm: bytes | None = None) -> Any:
        spoken.append(text)
        return Interruption("barge_in", [], 1.0, 0.0)  # the user talks over it

    s._speak_line = speak  # type: ignore[method-assign]
    feed.waiting.append(Notice(3, "The 13th prime is 41. I ran primes.py."))

    async def listen(**kw: Any) -> Any:
        if kw.get("audio") is None:  # idle listening: nobody speaks yet
            await asyncio.sleep(10)
        return turn("stop")

    client.listen_turn = listen
    heard, over = await s.hear_idle()
    assert over is True and heard.text == "stop" and feed.acked == [3]
    assert feed.waiting == [] and spoken == ["Your earlier request is done: The 13th prime is 41."]
    # back to idle listening: nothing is spoken again
    task = asyncio.create_task(s.hear_idle())
    await asyncio.sleep(0.5)
    task.cancel()
    assert len(spoken) == 1


def test_no_notice_starts_soon_after_the_user_spoke_or_interrupted() -> None:
    s, client = _playing_session()
    now = time.perf_counter()
    assert s._user_idle()
    s._speech_mark = now - 1.0  # spoke a second ago
    assert not s._user_idle()
    s._speech_mark = now - 3.5
    assert s._user_idle()
    s._quiet_notices()  # interrupted or said stop just now
    assert not s._user_idle()
    s._no_notice_until = now - 0.1
    assert s._user_idle()
    client.last_voiced = time.perf_counter()  # voiced block in this listen
    assert not s._user_idle()


async def test_a_barge_in_or_a_stop_word_starts_the_quiet_period(player: Player) -> None:
    s, client = _playing_session()
    client.hub.put(0.4, 0.4, 0.4, 0.4, 0.4)
    assert await s._speak_line("A notice.") is not None
    assert s._no_notice_until > time.perf_counter() + 1.0  # NOTICE_COOLDOWN_S is 1.5
    s2, _ = _playing_session()
    assert s2._no_notice_until == 0.0


async def test_hearing_a_turn_marks_the_end_of_the_users_speech() -> None:
    s, client = _playing_session()
    end = time.perf_counter() - 0.2

    async def listen(**kw: Any) -> Any:
        from dataclasses import replace

        return replace(turn("hello"), t_speech_end=end)

    client.listen_turn = listen
    await s.hear()
    assert s._speech_mark == end and not s._user_idle()


async def test_notices_are_cut_by_the_same_onset_guard_as_answers(player: Player) -> None:
    """Same blocks, same number consumed before the cut: a notice is not stopped by the stop-word
    transcript (S4: onset to fire 0.4-0.6 s in stack-1105-1517, answers 0.25 s in 1105-1426)."""
    blocks = (0.001, 0.001, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4)

    s, client = _playing_session()
    client.hub.put(*blocks)
    assert await s._speak_line("A notice.") is not None
    left_notice = client.hub.queue.qsize()

    s2, client2 = _playing_session()
    client2.hub.put(*blocks)

    async def handle(idx: int, heard: Any) -> None:
        s2._answer_started = time.perf_counter()
        await asyncio.sleep(10)

    s2.handle = handle  # type: ignore[method-assign]
    result = await asyncio.wait_for(s2.respond(1, turn("tell me about volcanoes")), timeout=5)
    assert result is not None and result.kind == "barge_in"
    assert client2.hub.queue.qsize() == left_notice


def _notice_rows(s: Any) -> list[dict[str, Any]]:
    return [r for r in s.records if "notice" in r]


async def test_a_played_notice_is_logged_with_job_text_and_outcome(player: Player) -> None:
    s, client = _playing_session()
    player.play_s = 0.05
    feed = s.notice_feed = Feed()

    async def listen(**kw: Any) -> Any:
        await asyncio.sleep(0.6)
        return turn("hello")

    client.listen_turn = listen
    feed.waiting.append(Notice(4, "The 13th prime is 41. I ran it.", job="job9"))
    await s.hear_idle()
    (row,) = _notice_rows(s)
    assert row["notice"] == 4 and row["job"] == "job9" and row["outcome"] == "played"
    assert row["spoken"] == "Your earlier request is done: The 13th prime is 41."
    assert row["waited_s"] >= 0 and "ts" in row


async def test_a_cut_notice_is_logged_as_cut(player: Player) -> None:
    s, client = _playing_session()
    feed = s.notice_feed = Feed()

    async def speak(text: str, pcm: bytes | None = None) -> Any:
        return Interruption("barge_in", [], 2.5, 0.0)

    s._speak_line = speak  # type: ignore[method-assign]

    async def listen(**kw: Any) -> Any:
        if kw.get("audio") is None:
            await asyncio.sleep(10)
        return turn("stop")

    client.listen_turn = listen
    feed.waiting.append(Notice(5, "Done it.", job="job2"))
    await s.hear_idle()
    (row,) = _notice_rows(s)
    assert row["outcome"] == "cut" and row["job"] == "job2" and row["cancel_ms"] == 2.5


async def test_a_notice_held_back_is_logged_once_with_the_reason(player: Player) -> None:
    s, client = _playing_session()
    feed = s.notice_feed = Feed()
    s._quiet_notices()  # the user just interrupted something
    spoken: list[str] = []

    async def speak(text: str, pcm: bytes | None = None) -> Any:
        spoken.append(text)

    s._speak_line = speak  # type: ignore[method-assign]

    async def listen(**kw: Any) -> Any:
        await asyncio.sleep(10)

    client.listen_turn = listen
    feed.waiting.append(Notice(6, "Done.", job="job3"))
    task = asyncio.create_task(s.hear_idle())
    await asyncio.sleep(0.7)
    task.cancel()
    rows = _notice_rows(s)
    assert [r["outcome"] for r in rows] == ["held"] and rows[0]["reason"] == "after_interruption"
    assert rows[0]["job"] == "job3" and spoken == []


def test_the_block_reasons_are_named() -> None:
    s, client = _playing_session()
    assert s._notice_block() is None
    client.last_voiced = time.perf_counter()
    assert s._notice_block() == "user_speaking"
    client.last_voiced = 0.0
    s._speech_mark = time.perf_counter()
    assert s._notice_block() == "after_user_speech"
