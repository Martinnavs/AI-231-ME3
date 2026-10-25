"""Fillers in a doc_chat turn: timed lines, the tool line, and giving up (R2)."""

import asyncio
import threading
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from test_bargein import session, turn  # noqa: E402

from doc_chat.__main__ import Session  # noqa: E402
from doc_chat.fillers import Fillers, TurnFillers  # noqa: E402

CONFIG = {
    "timing": {"stall_s": 0.02, "notice_s": 0.05, "giveup_s": 0.1},
    "stall": ["Mm, one moment."],
    "notice": ["Sorry, this is taking a little longer than usual."],
    "giveup": ["Sorry, that's taking too long. Could you ask me again?"],
    "default": ["One moment."],
    "tools": {"weather": ["Let me check the weather."]},
}


def _fillers() -> Fillers:
    f = Fillers(CONFIG)
    f.audio = {line: line.encode() for line in f.lines()}
    return f


def _with_fillers() -> tuple[Session, list[bytes]]:
    s, _client = session()
    s.fillers = _fillers()
    s._turn_fillers = TurnFillers(s.fillers)
    pushed: list[bytes] = []
    s._filler_push = pushed.append
    return s, pushed


async def test_a_slow_turn_plays_the_stall_then_the_notice_then_gives_up() -> None:
    s, pushed = _with_fillers()
    t0 = asyncio.get_running_loop().time()
    import time

    result = await asyncio.wait_for(s._filler_timer(time.perf_counter()), timeout=2)
    assert result == "giveup"
    assert pushed == [b"Mm, one moment.", b"Sorry, this is taking a little longer than usual."]
    assert s._filler_lines == [
        "Mm, one moment.",
        "Sorry, this is taking a little longer than usual.",
    ]
    assert s._filler_started is not None and asyncio.get_running_loop().time() - t0 < 1


async def test_nothing_plays_once_the_answer_has_started() -> None:
    import time

    s, pushed = _with_fillers()
    s._answer_started = time.perf_counter()
    timer = asyncio.create_task(s._filler_timer(time.perf_counter()))
    await asyncio.sleep(0.2)
    assert not timer.done() and pushed == []  # waits quietly until the turn ends
    timer.cancel()


async def test_a_tool_event_from_another_thread_plays_that_tool_s_line() -> None:
    s, pushed = _with_fillers()
    s._loop = asyncio.get_running_loop()
    worker = threading.Thread(target=s.on_agent_event, args=({"event": "tool", "name": "weather"},))
    worker.start()
    worker.join()
    await asyncio.sleep(0.01)
    assert pushed == [b"Let me check the weather."]
    s.on_agent_event({"event": "tool", "name": "weather"})  # one per turn
    await asyncio.sleep(0.01)
    assert pushed == [b"Let me check the weather."]


async def test_giving_up_drops_the_turn_and_logs_it() -> None:
    s, client = session()
    s.fillers = _fillers()
    played: list[str] = []

    async def handle(idx: int, heard: Any) -> None:
        await asyncio.sleep(10)  # the model never answers

    async def play_pcm(text: str, pcm: bytes) -> None:
        played.append(text)

    s.handle = handle  # type: ignore[method-assign]
    s.play_pcm = play_pcm  # type: ignore[method-assign]
    result = await asyncio.wait_for(s.respond(1, turn("will it rain")), timeout=5)
    assert result is None
    assert played == ["Sorry, that's taking too long. Could you ask me again?"]
    assert s.records[-1]["gave_up"] is True
    assert client.tts_cancels == 1


async def test_the_notice_waits_for_the_stall_to_finish_and_a_gap() -> None:
    import time

    s, pushed = _with_fillers()
    s.fillers.gap_s = 0.15  # type: ignore[union-attr]
    s.fillers.giveup_s = 5.0  # type: ignore[union-attr]
    times: list[float] = []
    inner = s._filler_push
    s._filler_push = lambda pcm: (times.append(time.perf_counter()), inner(pcm))[1]  # type: ignore[misc]
    s.client.tts_sample_rate = 15  # the 15-byte stall line then lasts 0.5 s
    timer = asyncio.create_task(s._filler_timer(time.perf_counter()))
    await asyncio.sleep(1.2)
    timer.cancel()
    assert len(times) == 2 and times[1] - times[0] >= 0.5 + 0.15 - 0.01


async def test_the_say_is_speech_not_the_answer_and_timed_lines_follow_it() -> None:
    import time

    s, pushed = _with_fillers()
    s.client.tts_sample_rate = 1000  # the 15-byte stall line lasts 7.5 ms
    s._loop = asyncio.get_running_loop()
    s.on_agent_event({"event": "tool", "name": "weather", "said": True})
    await asyncio.sleep(0.01)
    assert s._turn_fillers is not None and s._turn_fillers.say_pending
    timer = asyncio.create_task(s._filler_timer(time.perf_counter()))
    await asyncio.sleep(0.15)
    assert pushed == []  # the stall waits for the announced say
    s._current_is_say = True
    s._chunk(None)(b"\x00\x00" * 10)  # the say's audio starts
    assert s._answer_started is None and s._say_started is not None
    assert not s._turn_fillers.say_pending
    await asyncio.sleep(0.2)
    assert pushed[0] == b"Mm, one moment."  # the stall, only after the say began
    timer.cancel()
    s._current_is_say = False
    s._chunk(None)(b"\x00\x00")  # a real answer sentence
    assert s._answer_started is not None


async def test_each_filler_is_logged_with_when_it_starts_and_how_long_it_is() -> None:
    import time

    s, _pushed = _with_fillers()
    s.client.tts_sample_rate = 15  # the 15-byte stall line lasts 0.5 s
    s._t_final = time.perf_counter() - 1.0
    s._play_filler("stall")
    s._play_filler("notice")  # queued behind the stall
    first, second = s._filler_log
    assert first["kind"] == "stall" and 0.95 < first["start_s"] < 1.1 and first["len_s"] == 0.5
    assert second["start_s"] > first["start_s"] + 0.45
