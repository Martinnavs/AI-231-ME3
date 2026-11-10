"""The stall watchdog: the model says nothing after a tool result, so the turn is answered from
the result instead of being dropped by the client (2026-11-05: 16 s of silence)."""

import asyncio
from typing import Any

import pytest
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from voice_pipeline import question
from voice_pipeline.agent import STALL_GENERIC, TurnTap, stall_sentence

STATUS = {"cpu_temperature_c": 51.4, "free_memory_mb": 812, "uptime_minutes": 125}


class Recorder:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.interrupts = 0

    async def on_text(self, text: str) -> None:
        self.texts.append(text)

    async def interrupt(self) -> None:
        self.interrupts += 1


def _tap(rec: Recorder, stall_s: float = 0.05) -> TurnTap:
    tap = TurnTap(say_delay_s=0, stall_s=stall_s)
    tap.interrupt = rec.interrupt
    tap.begin(rec.on_text)
    return tap


async def _feed(tap: TurnTap, frame: Any) -> None:
    """Hand a frame to the tap as the pipeline would (what it forwards is dropped)."""

    async def push(*_args: Any, **_kwargs: Any) -> None:
        return None

    tap.push_frame = push  # type: ignore[method-assign]
    await tap.process_frame(frame, FrameDirection.DOWNSTREAM)


def _call(name: str) -> FunctionCallsStartedFrame:
    call = FunctionCallFromLLM(function_name=name, tool_call_id="1", arguments={}, context=None)
    return FunctionCallsStartedFrame(function_calls=[call])


def _result(name: str, result: Any) -> FunctionCallResultFrame:
    return FunctionCallResultFrame(
        function_name=name, tool_call_id="1", arguments={}, result=result, properties=None
    )


@pytest.fixture(autouse=True)
def _asked() -> Any:
    before = question.current
    question.current = "how hot is the pi"
    yield
    question.current = before


async def test_a_model_that_stalls_after_a_tool_is_answered_from_the_result() -> None:
    rec = Recorder()
    tap = _tap(rec)
    await _feed(tap, LLMFullResponseStartFrame())
    await _feed(tap, _call("device_status"))
    await _feed(tap, _result("device_status", STATUS))
    await _feed(
        tap, LLMFullResponseEndFrame()
    )  # the call response ended; the follow-up never talks
    assert not tap.done.is_set()
    await asyncio.wait_for(tap.done.wait(), 1)
    assert rec.texts == ["The Pi is at 51 degrees Celsius. "]
    assert rec.interrupts == 1
    assert tap.stall_fallback == {
        "tool": "device_status",
        "after_s": 0.05,
        "text": "The Pi is at 51 degrees Celsius.",
    }
    assert tap.first_text is not None


async def test_the_fallback_is_logged_as_stall_fallback_in_the_turn_result() -> None:
    from voice_pipeline.agent import Agent

    rec = Recorder()

    class Worker:
        async def queue_frame(self, frame: Any) -> None:
            return None

    agent = Agent.__new__(Agent)  # no Gemini: the model is the test, which stays silent
    agent.tap = _tap(rec)
    agent.worker = Worker()  # type: ignore[assignment]
    asking = asyncio.create_task(
        agent.ask(
            [{"role": "user", "content": "how hot is the pi"}], reset=True, on_text=rec.on_text
        )
    )
    await asyncio.sleep(0)
    await _feed(agent.tap, _call("device_status"))
    await _feed(agent.tap, _result("device_status", STATUS))
    result = await asyncio.wait_for(asking, 2)
    assert result["status"] == "ok"
    assert result["stall_fallback"]["tool"] == "device_status"
    assert result["text"] == "The Pi is at 51 degrees Celsius. "


async def test_text_from_the_model_cancels_the_watchdog() -> None:
    rec = Recorder()
    tap = _tap(rec, stall_s=0.1)
    await _feed(tap, _call("web_answer"))
    await _feed(tap, _result("web_answer", "Paris is the capital."))
    await _feed(tap, LLMTextFrame(text="It is Paris."))
    await asyncio.sleep(0.25)
    assert rec.texts == ["It is Paris."] and rec.interrupts == 0
    assert tap.stall_fallback is None and not tap.done.is_set()


async def test_a_second_tool_call_is_not_a_stall() -> None:
    rec = Recorder()
    tap = _tap(rec, stall_s=0.1)
    await _feed(tap, _call("weather"))
    await _feed(tap, _result("weather", "Sunny."))
    await _feed(tap, _call("web_answer"))  # the model chains another call
    await asyncio.sleep(0.25)
    assert rec.texts == [] and tap.stall_fallback is None


async def test_the_watch_waits_for_every_result_of_a_parallel_call() -> None:
    rec = Recorder()
    tap = _tap(rec, stall_s=0.1)
    calls = [
        FunctionCallFromLLM(function_name=n, tool_call_id=n, arguments={}, context=None)
        for n in ("weather", "web_answer")
    ]
    await _feed(tap, FunctionCallsStartedFrame(function_calls=calls))
    await _feed(tap, _result("weather", "Sunny."))
    await asyncio.sleep(0.25)  # the second tool is just slow
    assert tap.stall_fallback is None


async def test_a_new_turn_drops_a_pending_watch() -> None:
    rec = Recorder()
    tap = _tap(rec, stall_s=0.1)
    await _feed(tap, _call("device_status"))
    await _feed(tap, _result("device_status", STATUS))
    tap.begin(rec.on_text)
    await asyncio.sleep(0.25)
    assert rec.texts == [] and tap.stall_fallback is None


def test_the_sentence_comes_from_the_tool_s_own_text_or_an_apology() -> None:
    assert stall_sentence("device_status", STATUS, "how long has it been up") == (
        "It has been up for 2 hours 5 minutes."
    )
    assert stall_sentence("web_answer", "Paris is the capital of France.", "x") == (
        "Paris is the capital of France."
    )
    assert stall_sentence("set_volume", "The speaker volume is now 40 percent.", "x") == (
        "The speaker volume is now 40 percent."
    )
    assert stall_sentence("lights", {"ok": True, "message": "The lights are on."}, "x") == (
        "The lights are on."
    )
    assert stall_sentence("web_answer", "I couldn't find that.", "x") == STALL_GENERIC
    assert stall_sentence("device_status", {"junk": 1}, "x") == STALL_GENERIC
    assert stall_sentence("delegate", {"ok": False, "message": "no"}, "x") == STALL_GENERIC
    assert (
        stall_sentence("web_research", "<untrusted_search_data>news</untrusted_search_data>", "x")
        == STALL_GENERIC
    )


def test_web_research_stall_window_covers_worst_case_first_chunk_retry() -> None:
    from voice_pipeline import agent

    worst_case_retry = agent.RETRY_FIRST_CHUNK_S + agent.STREAM_IDLE_S
    web_research_stall = agent.STALL_BY_TOOL["web_research"]
    assert web_research_stall > worst_case_retry
    assert web_research_stall < agent.TURN_TIMEOUT_S
