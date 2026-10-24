"""The tools' `say` argument: offered to the model, spoken at the call, never sent to the tool."""

from types import SimpleNamespace
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallResultFrame,
    FunctionCallResultProperties,
    FunctionCallsStartedFrame,
)

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

    tap = TurnTap()
    tap.begin(on_text, on_tool, on_say)
    await tap._announce(_frame("weather", {"say": "Checking the weather."}))
    assert says == ["Checking the weather. "] and texts == [] and tools == [("weather", True)]
    assert tap.first_text is not None


async def test_without_a_say_callback_the_sentence_goes_out_as_text() -> None:
    texts: list[str] = []

    async def on_text(t: str) -> None:
        texts.append(t)

    tap = TurnTap()
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
    await tap._result(_result({"ok": True, "message": "recorded"}))
    assert texts == [] and tap.done.is_set()


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
