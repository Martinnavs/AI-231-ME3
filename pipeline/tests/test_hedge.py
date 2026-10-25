"""R4: two competing requests per turn; the first to answer wins and the other stays silent."""

import asyncio
from types import SimpleNamespace
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from voice_pipeline.hedge import HedgedAgent, HedgeGate, gated_tools


class FakeAgent:
    """Answers `text` after `delay` seconds unless cancelled first."""

    def __init__(self, delay: float, text: str, tool: str | None = None) -> None:
        self.delay, self.text, self.tool = delay, text, tool
        self.cancelled = asyncio.Event()
        self.asked = 0

    async def ask(self, messages: Any, *, reset: bool, on_text: Any, on_tool: Any, on_say: Any):
        self.asked += 1
        self.cancelled.clear()
        try:
            await asyncio.wait_for(self.cancelled.wait(), self.delay)
            return {"status": "ok", "text": "", "cancelled": True}
        except TimeoutError:
            pass
        if self.tool:
            await on_tool(self.tool, False)
        await on_text(self.text)
        return {"status": "ok", "text": self.text}

    async def cancel(self) -> None:
        self.cancelled.set()


def _hedged(agents: list[FakeAgent], after_s: float = 0.0) -> HedgedAgent:
    return HedgedAgent(agents, HedgeGate(), after_s)  # type: ignore[arg-type]


async def test_the_faster_request_wins_and_the_slower_one_is_silent_and_cancelled() -> None:
    slow, fast = FakeAgent(0.3, "slow"), FakeAgent(0.02, "fast")
    spoken: list[str] = []

    async def on_text(t: str) -> None:
        spoken.append(t)

    result = await _hedged([slow, fast]).ask([], reset=True, on_text=on_text)
    assert spoken == ["fast"] and result["text"] == "fast"
    assert result["hedge_winner"] == 1 and result["hedge_requests"] == 2
    assert slow.cancelled.is_set()


async def test_the_first_request_keeps_the_turn_when_it_is_first() -> None:
    first, second = FakeAgent(0.02, "one"), FakeAgent(0.3, "two")
    spoken: list[str] = []

    async def on_text(t: str) -> None:
        spoken.append(t)

    result = await _hedged([first, second]).ask([], reset=True, on_text=on_text)
    assert spoken == ["one"] and result["hedge_winner"] == 0


async def test_with_a_deadline_the_second_request_starts_only_when_the_first_is_slow() -> None:
    quick, spare = FakeAgent(0.02, "quick"), FakeAgent(0.02, "spare")
    result = await _hedged([quick, spare], after_s=0.2).ask([], reset=True, on_text=_noop)
    assert spare.asked == 0 and result["hedge_requests"] == 1
    stuck, spare = FakeAgent(0.5, "stuck"), FakeAgent(0.02, "spare")
    result = await _hedged([stuck, spare], after_s=0.1).ask([], reset=True, on_text=_noop)
    assert spare.asked == 1 and result["hedge_winner"] == 1 and result["text"] == "spare"


async def _noop(*args: Any) -> None:
    pass


async def test_only_the_winner_runs_a_tool() -> None:
    gate = HedgeGate()
    ran: list[int] = []
    callbacks: list[tuple[int, Any]] = []

    def schema(index: int) -> ToolsSchema:
        async def handler(params: Any) -> None:
            ran.append(index)
            await params.result_callback("done")

        base = FunctionSchema(
            name="set_volume", description="v", properties={}, required=[], handler=handler
        )
        return gated_tools(ToolsSchema(standard_tools=[base]), gate, index)

    async def result_callback(result: Any, *, properties: Any = None) -> None:
        callbacks.append((len(ran), properties))

    first, second = schema(0), schema(1)
    params = SimpleNamespace(result_callback=result_callback)
    assert gate.claim(1)  # request 1 got there first
    await first.standard_tools[0].handler(params)
    await second.standard_tools[0].handler(params)
    assert ran == [1]
    assert callbacks[0][1].run_llm is False  # the loser asked nothing of the model
