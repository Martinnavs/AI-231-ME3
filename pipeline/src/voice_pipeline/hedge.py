"""R4: send each LLM request twice and keep whichever answers first.

The probe (docs/LATENCY.md) saw one request p90 2.86 s, the faster of two p90 1.94 s: the provider
spikes now and then, and a second request is usually not stuck. `HedgedAgent` wraps several
`Agent`s (own pipeline, own Gemini request, same tools). The first request to produce anything, a
tool call or text, wins; the others are cancelled and nothing of theirs is forwarded. A tool has
side effects (volume, timers), so every tool handler is gated: only the winner may run it, and the
claim is made at the handler as well as at the callbacks, whichever comes first.

`after_s` > 0 starts the second request only if the first has produced nothing by then (half the
requests, a smaller gain); 0 starts both at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties

from voice_pipeline.agent import Agent, OnText, OnTool

LOSER_JOIN_S = 5.0  # how long a cancelled request is given to wind down


class HedgeGate:
    """Which request of the turn won: the first to claim."""

    def __init__(self) -> None:
        self.winner: int | None = None
        self.won = asyncio.Event()

    def reset(self) -> None:
        self.winner = None
        self.won.clear()

    def claim(self, index: int) -> bool:
        """True if `index` is (or has just become) the winner."""
        if self.winner is None:
            self.winner = index
            self.won.set()
        return self.winner == index


def gated_tools(tools: ToolsSchema, gate: HedgeGate, index: int) -> ToolsSchema:
    """A copy of `tools` whose handlers run only when request `index` is the winner."""
    gated: list[FunctionSchema] = []
    for schema in tools.standard_tools:
        inner = schema.handler
        assert inner is not None

        async def handler(params: Any, _inner: Any = inner) -> None:
            if gate.claim(index):
                await _inner(params)
            else:  # the other request won: do nothing, ask nothing of the model
                await params.result_callback(
                    {"ok": False, "message": "superseded"},
                    properties=FunctionCallResultProperties(run_llm=False),
                )

        gated.append(
            FunctionSchema(
                name=schema.name,
                description=schema.description,
                properties=schema.properties,
                required=schema.required,
                handler=handler,
            )
        )
    return ToolsSchema(standard_tools=gated)


class HedgedAgent:
    """The `Agent` interface (start, stop, ask, cancel) over several competing requests."""

    def __init__(self, agents: list[Agent], gate: HedgeGate, after_s: float = 0.0) -> None:
        self.agents = agents
        self.gate = gate
        self.after_s = after_s

    async def start(self) -> None:
        for agent in self.agents:
            await agent.start()

    async def stop(self) -> None:
        for agent in self.agents:
            await agent.stop()

    async def warm_up(self) -> None:
        """Touch every pipeline once so no request pays for a cold connection."""
        for agent in self.agents:
            await agent.ask([{"role": "user", "content": "Say OK."}], reset=True)

    async def cancel(self) -> None:
        for agent in self.agents:
            await agent.cancel()

    def _guard(self, index: int, callback: Any) -> Any:
        """`callback` for request `index`: forwards only while it is the winner."""
        if callback is None:
            return None

        async def guarded(*args: Any) -> None:
            if self.gate.claim(index):
                await callback(*args)

        return guarded

    async def ask(
        self,
        messages: list[Any],
        *,
        reset: bool,
        on_text: OnText | None = None,
        on_tool: OnTool | None = None,
        on_say: OnText | None = None,
    ) -> dict[str, Any]:
        self.gate.reset()
        started = time.perf_counter()
        tasks: dict[int, asyncio.Task[dict[str, Any]]] = {}

        def launch(i: int) -> None:
            tasks[i] = asyncio.create_task(
                self.agents[i].ask(
                    messages,
                    reset=reset,
                    on_text=self._guard(i, on_text),
                    on_tool=self._guard(i, on_tool),
                    on_say=self._guard(i, on_say),
                )
            )

        launch(0)
        if self.after_s > 0 and len(self.agents) > 1:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._first_signal(tasks), self.after_s)
            if self.gate.winner is None and not tasks[0].done():
                for i in range(1, len(self.agents)):
                    launch(i)
        else:
            for i in range(1, len(self.agents)):
                launch(i)
        await self._first_signal(tasks)
        if self.gate.winner is None:  # a request ended without saying anything
            self.gate.claim(next(i for i, t in tasks.items() if t.done()))
        winner = self.gate.winner
        assert winner is not None
        for i in tasks:
            if i != winner:
                await self.agents[i].cancel()
        result = await tasks[winner]
        for i, task in tasks.items():
            if i != winner:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, LOSER_JOIN_S)
        offset = 0.0 if winner == 0 else (self.after_s if self.after_s > 0 else 0.0)
        return {
            **result,
            "hedge_winner": winner,
            "hedge_requests": len(tasks),
            "hedge_offset_s": offset,
            "hedge_total_s": time.perf_counter() - started,
        }

    async def _first_signal(self, tasks: dict[int, asyncio.Task[dict[str, Any]]]) -> None:
        """Wait until a request has won or any request has ended."""
        claimed = asyncio.create_task(self.gate.won.wait())
        try:
            await asyncio.wait({claimed, *tasks.values()}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            claimed.cancel()
