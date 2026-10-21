"""Talking over the assistant: the detector, and what the session does when it happens."""

from __future__ import annotations

import argparse
import asyncio
import io
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

pytest.importorskip("langchain_core")

from doc_chat.__main__ import Interruption, Session  # noqa: E402
from doc_chat.bargein import OnsetDetector, PreRoll  # noqa: E402
from doc_chat.dialogue import ChatHistory  # noqa: E402
from doc_chat.speech import HeardTurn  # noqa: E402

SR = 16000


def block(rms: float) -> bytes:
    """100 ms of a 220 Hz tone with the given RMS level (0-1)."""
    t = np.arange(SR // 10) / SR
    return (rms * np.sqrt(2) * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16).tobytes()


# ---- the detector ----


def test_quiet_never_triggers() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    assert not any(d.feed(0.004, 0.1) for _ in range(100))


def test_speech_triggers_after_the_minimum_time() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    fired = [d.feed(0.2, 0.1) for _ in range(5)]
    assert fired == [False, False, True, True, True]


def test_a_click_or_cough_does_not_add_up() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    for _ in range(20):
        assert not d.feed(0.5, 0.1)  # a loud block counts 0.1 s...
        d.feed(0.001, 0.1)  # ...and quiet ones take it back
        d.feed(0.001, 0.1)


def test_a_dip_inside_a_word_does_not_reset_it() -> None:
    d = OnsetDetector(noise=0.005, ratio=4.0, min_s=0.3)
    d.feed(0.2, 0.1)
    d.feed(0.2, 0.1)
    d.feed(0.001, 0.1)  # a quiet moment
    assert d.feed(0.2, 0.1) is False
    assert d.feed(0.2, 0.1) is True


def test_the_threshold_never_drops_below_the_floor() -> None:
    assert OnsetDetector(noise=0.0001, ratio=4.0, floor=0.02).threshold == 0.02
    assert OnsetDetector(noise=0.02, ratio=4.0, floor=0.02).threshold == pytest.approx(0.08)


def test_preroll_keeps_only_the_latest_blocks() -> None:
    roll = PreRoll(seconds=0.3)
    for i in range(5):
        roll.push(bytes([i]))
    assert roll.blocks() == [bytes([2]), bytes([3]), bytes([4])]


# ---- the session ----


class FakeHub:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def put(self, *levels: float) -> None:
        for lv in levels:
            self.queue.put_nowait(block(lv))

    async def get(self) -> bytes:
        return await self.queue.get()

    def drain(self) -> int:
        return 0

    async def blocks(self) -> Any:
        while True:
            yield await self.get()


class FakeStream:
    cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakePlayer:
    aborted = False

    def abort(self) -> None:
        self.aborted = True


class FakeClient:
    tts_sample_rate = 24000
    noise_floor: float | None = 0.01

    def __init__(self) -> None:
        self.hub = FakeHub()
        self.tts_cancels = 0
        self.heard: list[HeardTurn] = []
        self.listen_audio: list[list[bytes]] = []
        self.joined: list[bytes] = []
        self.join_text = "joined sentence"

    async def send_tts_cancel(self) -> None:
        self.tts_cancels += 1

    async def listen_turn(self, **kw: Any) -> HeardTurn:
        audio = kw.get("audio")
        got: list[bytes] = []
        if audio is not None:
            async for blk in audio:  # take the lead-in, which is what the test looks at
                got.append(blk)
                if len(got) >= 3:
                    break
        self.listen_audio.append(got)
        return self.heard.pop(0)

    async def hear_pcm(self, pcm: bytes, **kw: Any) -> HeardTurn:
        self.joined.append(pcm)
        return turn(self.join_text)


def turn(text: str, pcm: bytes = b"", tail: bytes = b"") -> HeardTurn:
    return HeardTurn(text, None, None, 1.0, 2.0, 1.5, pcm, "", tail)


def session(**args: Any) -> tuple[Session, FakeClient]:
    client = FakeClient()
    ns = argparse.Namespace(
        play=False,
        voice="F1",
        slot="doc?",
        risk=None,
        vad_hold=0.6,
        barge_in=True,
        barge_ms=300,
        barge_ratio=4.0,
        **args,
    )
    s = Session(
        args=ns,
        client=client,  # type: ignore[arg-type]
        log_file=io.StringIO(),
        out_dir=None,
        history=ChatHistory(4),
    )
    return s, client


async def test_an_answer_nobody_interrupts_just_completes() -> None:
    s, _client = session()
    done: list[int] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        await asyncio.sleep(0.05)
        done.append(idx)

    s.handle = handle  # type: ignore[method-assign]
    assert await s.respond(1, turn("who is harry")) is None
    assert done == [1]


async def test_talking_before_the_answer_starts_means_you_were_cut_off() -> None:
    s, client = session()
    cancelled: list[bool] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        try:
            await asyncio.sleep(10)  # the model is still thinking
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    s.handle = handle  # type: ignore[method-assign]
    client.hub.put(0.001, 0.2, 0.2, 0.2, 0.2)
    result = await asyncio.wait_for(s.respond(1, turn("break from the document")), timeout=5)

    assert isinstance(result, Interruption) and result.kind == "resumed"
    assert cancelled == [True] and client.tts_cancels == 1
    assert s.history.pairs() == []  # nothing was answered, nothing is remembered
    rec = s.records[-1]
    assert rec["interrupted"] == "resumed" and rec["answer_words"] == 0
    assert rec["interrupt_after_answer_start_ms"] is None
    assert len(result.blocks) >= 3  # the start of what was said is kept


async def test_talking_over_the_answer_stops_it_and_remembers_what_was_said() -> None:
    s, client = session()
    stream, player = FakeStream(), FakePlayer()

    async def handle(idx: int, heard: HeardTurn) -> None:
        s._answer_started = 1.0
        s._spoken = ["Hermione is a witch.", "She is clever."]
        s._stream, s._player = stream, player  # type: ignore[assignment]
        await asyncio.sleep(10)

    s.handle = handle  # type: ignore[method-assign]

    async def talk_later() -> None:
        await asyncio.sleep(0.1)
        client.hub.put(0.3, 0.3, 0.3, 0.3)

    asyncio.create_task(talk_later())
    result = await asyncio.wait_for(s.respond(1, turn("who is hermione")), timeout=5)

    assert result is not None and result.kind == "barge_in"
    assert stream.cancelled and player.aborted and client.tts_cancels == 1
    assert s.history.pairs() == [("who is hermione", "Hermione is a witch. She is clever.")]
    rec = s.records[-1]
    assert (
        rec["interrupted"] == "barge_in" and rec["spoken"] == "Hermione is a witch. She is clever."
    )
    assert rec["cancel_to_silence_ms"] < 500


async def test_its_own_voice_leaking_into_the_microphone_does_not_stop_it() -> None:
    """While it speaks the bar is higher than while it thinks."""
    s, client = session()
    done: list[int] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        s._answer_started = 1.0
        await asyncio.sleep(0.3)
        done.append(idx)

    s.handle = handle  # type: ignore[method-assign]
    # noise 0.01: speech while thinking needs > 0.03; while speaking > 0.04 (4 x noise)
    client.hub.put(*([0.035] * 8))
    assert await s.respond(1, turn("who is hermione")) is None
    assert done == [1]


async def test_the_same_level_does_interrupt_while_it_is_only_thinking() -> None:
    s, client = session()

    async def handle(idx: int, heard: HeardTurn) -> None:
        await asyncio.sleep(10)

    s.handle = handle  # type: ignore[method-assign]
    client.hub.put(*([0.035] * 8))
    result = await asyncio.wait_for(s.respond(1, turn("who is hermione")), timeout=5)
    assert result is not None and result.kind == "resumed"


async def test_speech_that_went_on_while_the_server_answered_counts_at_once() -> None:
    """Audio held back after `finalize` (the tail) is heard first, before anything new."""
    s, client = session()

    async def handle(idx: int, heard: HeardTurn) -> None:
        await asyncio.sleep(10)

    s.handle = handle  # type: ignore[method-assign]
    tail = block(0.2) * 4  # 0.4 s of speech after the turn was ended
    result = await asyncio.wait_for(s.respond(1, turn("break from the document", tail=tail)), 5)
    assert result is not None and result.kind == "resumed"
    assert client.hub.queue.empty()  # nothing had to come from the microphone


async def test_without_a_hub_or_with_barge_in_off_nothing_listens() -> None:
    s, client = session()
    s.args.barge_in = False
    ran: list[int] = []

    async def handle(idx: int, heard: HeardTurn) -> None:
        ran.append(idx)

    s.handle = handle  # type: ignore[method-assign]
    client.hub.put(0.5, 0.5, 0.5, 0.5)
    assert await s.respond(1, turn("x")) is None and ran == [1]


async def test_a_cut_off_sentence_is_joined_to_what_you_said_next() -> None:
    s, client = session()
    previous = turn("Break from the document.", pcm=b"\x01\x00" * 100)
    client.heard.append(turn("what is tissue paper used for", pcm=b"\x02\x00" * 50))
    lead = [block(0.2)] * 3

    result = await s.follow_up(previous, Interruption("resumed", lead, 12.0, 700.0))

    assert result.text == "joined sentence"
    assert client.listen_audio[0] == lead  # listening began with what had been heard
    joined = client.joined[0]
    assert joined.startswith(b"\x01\x00" * 100) and joined.endswith(b"\x02\x00" * 50)
    assert len(joined) > 300 + 0  # a short silence sits between the parts


async def test_talking_over_the_answer_is_a_new_turn_not_a_join() -> None:
    s, client = session()
    previous = turn("Who is Hermione?", pcm=b"\x01\x00" * 100)
    new = turn("Actually, who is Ron?", pcm=b"\x02\x00" * 50)
    client.heard.append(new)

    result = await s.follow_up(previous, Interruption("barge_in", [block(0.3)] * 3, 12.0, 900.0))

    assert result is new and client.joined == []


async def test_the_mic_loop_resumes_a_cut_off_turn_under_the_same_number() -> None:
    s, client = session()
    s.args.ask_to_add = False
    seen: list[tuple[int, str]] = []
    calls = {"n": 0}

    async def handle(idx: int, heard: HeardTurn) -> None:
        calls["n"] += 1
        seen.append((idx, heard.text))
        if calls["n"] == 1:
            client.hub.put(0.2, 0.2, 0.2, 0.2)
            await asyncio.sleep(10)  # interrupted

    s.handle = handle  # type: ignore[method-assign]
    first = replace(turn("Break from the document.", pcm=b"\x01\x00" * 10))
    client.heard.extend([first, turn("what is tissue paper", pcm=b"\x02\x00" * 10), turn("quit")])
    # listen order: the first turn, the continuation (follow-up), then the next turn: "quit"
    await asyncio.wait_for(s.run_mic(), timeout=10)

    assert seen == [(1, "Break from the document."), (1, "joined sentence")]
