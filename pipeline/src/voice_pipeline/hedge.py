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
from collections.abc import Callable, Coroutine
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties

from voice_pipeline.agent import Agent, OnText, OnTool

LOSER_JOIN_S = 5.0  # how long a cancelled request is given to wind down
LOCAL_GRACE_S = 3.0  # how long a failed model request waits for the local competitor
HOLD_S = 1.0  # with a local competitor, the model may not claim the turn before it has decided,
# or this long: the model's first words are a short "Checking the weather." that would otherwise
# win a few tenths of a second before the router's complete answer
# A local competitor (the router, router.py): given `claim()`, it must call it before it speaks or
# changes anything, and give up (return None) when it answers False; it returns the turn's result
# dict, or None when it cannot answer and the model should.
Local = Callable[[Callable[[], bool]], Coroutine[Any, Any, dict[str, Any] | None]]


class HedgeGate:
    """Which request of the turn won: the first to claim."""

    def __init__(self) -> None:
        self.winner: int | None = None
        self.won = asyncio.Event()
        self.hold: asyncio.Event | None = None  # set when the local competitor has decided

    def reset(self) -> None:
        self.winner = None
        self.won.clear()
        self.hold = None

    async def released(self) -> None:
        """Wait until the model may claim (at once when there is no local competitor)."""
        if self.hold is not None:
            await self.hold.wait()

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
            await gate.released()
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

    def __init__(
        self, agents: list[Agent], gate: HedgeGate, after_s: float = 0.0, hold_s: float = HOLD_S
    ) -> None:
        self.agents = agents
        self.gate = gate
        self.after_s = after_s
        self.hold_s = hold_s  # 0: a plain race, the model may claim at once

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

    def hide(self, names: frozenset[str]) -> None:
        for agent in self.agents:
            agent.hide(names)

    async def cancel(self) -> None:
        for agent in self.agents:
            await agent.cancel()

    def _guard(self, index: int, callback: Any) -> Any:
        """`callback` for request `index`: forwards only while it is the winner."""
        if callback is None:
            return None

        async def guarded(*args: Any) -> None:
            await self.gate.released()
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
        local: Local | None = None,
    ) -> dict[str, Any]:
        self.gate.reset()
        started = time.perf_counter()
        tasks: dict[int, asyncio.Task[dict[str, Any]]] = {}
        local_index = len(self.agents)
        local_task: asyncio.Task[dict[str, Any] | None] | None = None
        if local is not None:
            local_task = asyncio.create_task(local(lambda: self.gate.claim(local_index)))
            if self.hold_s > 0:
                hold = self.gate.hold = asyncio.Event()
                local_task.add_done_callback(lambda _: hold.set())
                asyncio.get_running_loop().call_later(self.hold_s, hold.set)  # harmless if set

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
        if self.gate.winner is None and local_task is not None and not local_task.done():
            # a model request ended without saying anything (no network): the router may still
            # answer, so give it the time it needs before the failure becomes the turn's result
            await asyncio.wait({local_task}, timeout=LOCAL_GRACE_S)
        if self.gate.winner is None:  # a request ended without saying anything
            self.gate.claim(next(i for i, t in tasks.items() if t.done()))
        winner = self.gate.winner
        assert winner is not None
        if local_task is not None and winner != local_index:
            local_task.cancel()  # the model answered first: the router's answer is not wanted
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await local_task
        for i in tasks:
            if i != winner:
                await self.agents[i].cancel()
        if local_task is not None and winner == local_index:
            local_result = await local_task
            assert local_result is not None
            for task in tasks.values():
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, LOSER_JOIN_S)
            return {
                **local_result,
                "hedge_winner": "router",
                "hedge_requests": len(tasks),
                "hedge_total_s": time.perf_counter() - started,
            }
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
