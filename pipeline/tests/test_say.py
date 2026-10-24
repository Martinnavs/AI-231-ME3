"""The tools' `say` argument: offered to the model, spoken at the call, never sent to the tool."""

from types import SimpleNamespace
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import FunctionCallFromLLM, FunctionCallsStartedFrame

from voice_pipeline.agent import TurnTap
from voice_pipeline.agent_tools import _recorded, build_tools


async def test_every_tool_offers_say_and_the_tool_never_gets_it() -> None:
    seen: list[dict[str, Any]] = []

    async def inner(params: Any) -> None:
        seen.append(dict(params.arguments))

    schema = FunctionSchema(
        name="weather", description="w", properties={}, required=[], handler=inner
    )
    record: list[dict[str, Any]] = []
    wrapped = _recorded(schema, record)
    assert "say" in wrapped.properties
    params = SimpleNamespace(arguments={"place": "Baguio", "say": "Checking the weather."})
    await wrapped.handler(params)
    assert seen == [{"place": "Baguio"}] and record == [
        {"tool": "weather", "args": {"place": "Baguio"}}
    ]
    tools = await build_tools([])
    assert "say" in tools.standard_tools[0].properties  # smart_home too


def _frame(name: str, args: dict[str, Any]) -> FunctionCallsStartedFrame:
    call = FunctionCallFromLLM(function_name=name, tool_call_id="1", arguments=args, context=None)
    return FunctionCallsStartedFrame(function_calls=[call])


async def test_the_model_s_say_is_spoken_at_the_call() -> None:
    texts: list[str] = []
    tools: list[str] = []

    async def on_text(t: str) -> None:
        texts.append(t)

    async def on_tool(n: str) -> None:
        tools.append(n)

    tap = TurnTap()
    tap.begin(on_text, on_tool)
    await tap._announce(_frame("weather", {"say": "Checking the weather."}))
    assert texts == ["Checking the weather. "] and tools == []
    assert tap.first_text is not None


async def test_without_say_the_client_is_told_which_tool_runs() -> None:
    tools: list[str] = []

    async def on_tool(n: str) -> None:
        tools.append(n)

    tap = TurnTap()
    tap.begin(None, on_tool)
    await tap._announce(_frame("weather", {"place": "Baguio"}))
    assert tools == ["weather"]
