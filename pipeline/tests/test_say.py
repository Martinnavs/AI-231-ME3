"""The tools' `say` argument: offered to the model, spoken at the call, never sent to the tool."""

import asyncio
from types import SimpleNamespace
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallResultFrame,
    FunctionCallResultProperties,
    FunctionCallsStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from voice_pipeline.agent import TurnTap
from voice_pipeline.agent_tools import _recorded, build_tools


async def test_every_tool_offers_say_and_the_tool_never_gets_it() -> None:
    seen: list[dict[str, Any]] = []

    async def inner(params: Any) -> None:
        seen.append(dict(params.arguments))

    async def deliver(result: Any, *, properties: Any = None) -> None:
        pass

    schema = FunctionSchema(
        name="weather", description="w", properties={}, required=[], handler=inner
    )
    record: list[dict[str, Any]] = []
    wrapped = _recorded(schema, record)
    assert "say" in wrapped.properties
    params = SimpleNamespace(
        arguments={"place": "Baguio", "say": "Checking the weather."}, result_callback=deliver
    )
    await wrapped.handler(params)
    assert seen == [{"place": "Baguio"}] and record == [
        {"tool": "weather", "args": {"place": "Baguio"}}
    ]
    tools = await build_tools([])
    assert "say" in tools.standard_tools[0].properties  # smart_home too


def _frame(name: str, args: dict[str, Any]) -> FunctionCallsStartedFrame:
    call = FunctionCallFromLLM(function_name=name, tool_call_id="1", arguments=args, context=None)
    return FunctionCallsStartedFrame(function_calls=[call])


async def test_the_model_s_say_is_its_own_event_and_the_tool_is_always_reported() -> None:
    texts: list[str] = []
    says: list[str] = []
    tools: list[tuple[str, bool]] = []

    async def on_text(t: str) -> None:
        texts.append(t)

    async def on_say(t: str) -> None:
        says.append(t)

    async def on_tool(n: str, said: bool) -> None:
        tools.append((n, said))

    tap = TurnTap(say_delay_s=0)
    tap.begin(on_text, on_tool, on_say)
    await tap._announce(_frame("weather", {"say": "Checking the weather."}))
    assert says == ["Checking the weather. "] and texts == [] and tools == [("weather", True)]
    assert tap.first_text is not None


async def test_without_a_say_callback_the_sentence_goes_out_as_text() -> None:
    texts: list[str] = []

    async def on_text(t: str) -> None:
        texts.append(t)

    tap = TurnTap(say_delay_s=0)
    tap.begin(on_text)
    await tap._announce(_frame("weather", {"say": "Checking the weather."}))
    assert texts == ["Checking the weather. "]


async def test_without_say_the_client_is_told_which_tool_runs() -> None:
    tools: list[tuple[str, bool]] = []

    async def on_tool(n: str, said: bool) -> None:
        tools.append((n, said))

    tap = TurnTap()
    tap.begin(None, on_tool)
    await tap._announce(_frame("weather", {"place": "Baguio"}))
    assert tools == [("weather", False)]


def _result(result: Any, quiet: bool = True) -> FunctionCallResultFrame:
    props = FunctionCallResultProperties(run_llm=False) if quiet else None
    return FunctionCallResultFrame(
        function_name="t", tool_call_id="1", arguments={}, result=result, properties=props
    )


async def _tap(calls: int, said: bool, texts: list[str]) -> TurnTap:
    async def on_text(t: str) -> None:
        texts.append(t)

    tap = TurnTap()
    tap.begin(on_text)
    tap.calls_started, tap.said = calls, said
    return tap


async def test_a_sentence_result_is_spoken_and_ends_the_turn_without_a_second_request() -> None:
    texts: list[str] = []
    tap = await _tap(1, True, texts)
    frame = _result("The speaker volume is now 60 percent.")
    await tap._result(frame)
    assert texts == ["The speaker volume is now 60 percent. "] and tap.done.is_set()
    assert frame.properties.run_llm is False


async def test_a_command_that_worked_ends_on_the_model_s_say() -> None:
    texts: list[str] = []
    tap = await _tap(1, True, texts)
    tap.say_text = "Turning on the lights."  # held, not yet sent
    await tap._result(_result({"ok": True, "message": "recorded"}))
    assert texts == ["Turning on the lights. "] and tap.done.is_set()


async def test_without_a_say_or_with_several_calls_the_model_still_answers() -> None:
    for calls, said in ((1, False), (2, True)):
        tap = await _tap(calls, said, [])
        frame = _result({"ok": True})
        await tap._result(frame)
        assert not tap.done.is_set() and frame.properties.run_llm is None
    tap = await _tap(1, False, [])
    await tap._result(_result("Sunny.", quiet=False))  # no request for quiet: untouched
    assert not tap.done.is_set()


async def test_spoken_result_tools_ask_for_no_second_request() -> None:
    got: list[Any] = []

    async def inner(params: Any) -> None:
        await params.result_callback("The speaker volume is 40 percent.")

    async def deliver(result: Any, *, properties: Any = None) -> None:
        got.append((result, properties))

    schema = FunctionSchema(
        name="get_volume", description="v", properties={}, required=[], handler=inner
    )
    params = SimpleNamespace(arguments={"say": "Checking."}, result_callback=deliver)
    await _recorded(schema, []).handler(params)
    assert got[0][1].run_llm is False
    got.clear()
    other = FunctionSchema(
        name="get_time", description="t", properties={}, required=[], handler=inner
    )
    params = SimpleNamespace(arguments={}, result_callback=deliver)
    await _recorded(other, []).handler(params)
    assert got[0][1] is None


async def test_a_failure_sentence_still_goes_to_the_model() -> None:
    got: list[Any] = []

    async def inner(params: Any) -> None:
        await params.result_callback("I couldn't find a Wikipedia article about Nobel lists.")

    async def deliver(result: Any, *, properties: Any = None) -> None:
        got.append(properties)

    schema = FunctionSchema(
        name="wiki_summary", description="w", properties={}, required=[], handler=inner
    )
    params = SimpleNamespace(arguments={}, result_callback=deliver)
    await _recorded(schema, []).handler(params)
    assert got == [None]


async def _announced(delay: float, texts: list[str]) -> TurnTap:
    async def on_text(t: str) -> None:
        texts.append(t)

    tap = TurnTap(say_delay_s=delay)
    tap.begin(on_text)
    await tap._announce(_frame("wiki_summary", {"say": "Looking that up."}))
    tap.calls_started = 1
    return tap


async def test_the_say_is_held_and_dropped_when_the_answer_arrives_in_time() -> None:
    texts: list[str] = []
    tap = await _announced(0.2, texts)
    assert texts == []  # held
    await tap._result(_result("Steinbeck wrote The Grapes of Wrath."))
    await asyncio.sleep(0.3)
    assert texts == ["Steinbeck wrote The Grapes of Wrath. "]  # the say never goes out


async def test_the_say_goes_out_when_the_tool_is_slow() -> None:
    texts: list[str] = []
    await _announced(0.05, texts)
    await asyncio.sleep(0.15)
    assert texts == ["Looking that up. "]


async def test_the_say_goes_out_before_the_model_s_own_words() -> None:
    from pipecat.frames.frames import LLMTextFrame

    texts: list[str] = []
    tap = await _announced(5.0, texts)
    await tap.process_frame(LLMTextFrame(text="Sorry, "), FrameDirection.DOWNSTREAM)
    assert texts[0] == "Looking that up. " and texts[1] == "Sorry, "


async def test_a_slow_tool_s_say_is_spoken_at_once_not_held() -> None:
    texts: list[str] = []

    async def on_text(t: str) -> None:
        texts.append(t)

    tap = TurnTap(say_delay_s=5.0)  # a long hold for tools in general
    tap.begin(on_text)
    await tap._announce(_frame("weather", {"say": "Checking the weather."}))
    assert texts == ["Checking the weather. "]  # weather has no hold
    texts.clear()
    tap.begin(on_text)
    await tap._announce(_frame("wiki_summary", {"say": "Looking that up."}))
    assert texts == []  # others are still held


class _Sink:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts

    async def say(self, text: str) -> None:
        self.texts.append(text)


async def test_the_slow_tool_table_speaks_lookups_and_documents_at_once() -> None:
    from voice_pipeline.agent import SAY_DELAY_SLOW, SAY_DELAY_TODAY

    for tool, now in (("web_answer", True), ("search_docs", True), ("delegate", True),
                      ("delegate_status", False), ("get_volume", False)):  # fmt: skip
        for table, expected in ((SAY_DELAY_SLOW, now), (SAY_DELAY_TODAY, False)):
            texts: list[str] = []

            tap = TurnTap(say_delay_s=5.0, say_delays=table)
            sink = _Sink(texts)
            tap.begin(sink.say)
            await tap._announce(_frame(tool, {"say": "One moment."}))
            assert (texts == ["One moment. "]) is (expected or tool == "weather"), (tool, table)
            tap.begin(None)  # stops a held say's timer


async def test_the_default_table_speaks_a_web_answer_say_at_once_and_holds_the_rest() -> None:
    from voice_pipeline.agent import SAY_DELAY_BY_TOOL

    for tool, now in (("web_answer", True), ("weather", True), ("search_docs", False),
                      ("delegate", False), ("wiki_summary", False)):  # fmt: skip
        texts: list[str] = []
        tap = TurnTap(say_delay_s=5.0, say_delays=SAY_DELAY_BY_TOOL)
        tap.begin(_Sink(texts).say)
        await tap._announce(_frame(tool, {"say": "One moment."}))
        assert (texts == ["One moment. "]) is now, tool
        tap.begin(None)
