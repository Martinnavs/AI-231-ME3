"""The agent: one long-lived Pipecat pipeline around native Gemini and the agent's tools.

`Agent.ask` runs one turn and reports its timings; text is streamed to an optional callback.
`Agent.cancel` interrupts the turn in progress. The dry run and the agent server both use it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMMessagesUpdateFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.google.llm import GoogleLLMService
from pipecat.workers.runner import WorkerRunner

MODEL = "gemini-3.1-flash-lite"
TURN_TIMEOUT_S = 20.0
OnText = Callable[[str], Awaitable[None]]
OnTool = Callable[[str, bool], Awaitable[None]]  # tool name, whether its `say` was spoken


def read_key(secret_dir: str) -> str:
    folder = Path.home() / ".secrets" / secret_dir
    for name in ("api-key.txt", "api_key.txt"):
        path = folder / name
        if path.exists():
            return "".join(path.read_text().split())
    raise SystemExit(f"no key in ~/.secrets/{secret_dir}/")


def build_llm(system: str, model: str = MODEL) -> GoogleLLMService:
    return GoogleLLMService(
        api_key=read_key("google-ai-studio"),
        settings=GoogleLLMService.Settings(model=model, system_instruction=system),
    )


class TurnTap(FrameProcessor):
    """Times one turn (first tool call, first text, end) and forwards its text."""

    def __init__(self) -> None:
        super().__init__()
        self.done = asyncio.Event()
        self.on_text: OnText | None = None
        self.on_say: OnText | None = None
        self.on_tool: OnTool | None = None
        self.begin(None)

    def begin(
        self,
        on_text: OnText | None,
        on_tool: OnTool | None = None,
        on_say: OnText | None = None,
    ) -> None:
        self.done.clear()
        self.on_text = on_text
        self.on_tool = on_tool
        self.on_say = on_say
        self.announced = False  # the first tool call of a turn is announced once
        self.text: list[str] = []
        self.started = time.perf_counter()
        self.first_call: float | None = None
        self.first_text: float | None = None
        self.calls_in_response = False
        self.responding = False  # a late end frame from a cancelled turn must not end this one

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        now = time.perf_counter() - self.started
        if isinstance(frame, LLMFullResponseStartFrame):
            self.calls_in_response = False
            self.responding = True
        elif isinstance(frame, FunctionCallsStartedFrame):
            self.calls_in_response = True
            if self.first_call is None:
                self.first_call = now
            if not self.announced:
                self.announced = True
                await self._announce(frame)
        elif isinstance(frame, LLMTextFrame):
            if self.first_text is None and frame.text.strip():
                self.first_text = now
            self.text.append(frame.text)
            if self.on_text is not None:
                await self.on_text(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame) and self.responding:
            if not self.calls_in_response:
                self.done.set()
        await self.push_frame(frame, direction)

    async def _announce(self, frame: FunctionCallsStartedFrame) -> None:
        """Speak the model's `say` sentence for the call (as its own event, `on_say`, when the
        client has one) and always tell the client which tool runs, and whether it was said."""
        calls = list(frame.function_calls)
        say = next((str(c.arguments.get("say") or "").strip() for c in calls), "")
        speak = self.on_say or self.on_text
        if say and speak is not None:
            self.text.append(say + " ")
            if self.first_text is None:
                self.first_text = time.perf_counter() - self.started
            await speak(say + " ")
        if calls and self.on_tool is not None:
            await self.on_tool(calls[0].function_name, bool(say and speak is not None))


class Agent:
    """One pipeline, one connection: user aggregator -> LLM -> tap -> assistant aggregator."""

    def __init__(self, llm: GoogleLLMService, tools: ToolsSchema) -> None:
        pair = LLMContextAggregatorPair(LLMContext(tools=tools))
        self.tap = TurnTap()
        # No idle timeout: a voice session can sit quiet for minutes between turns.
        self.worker = PipelineWorker(
            Pipeline([pair.user(), llm, self.tap, pair.assistant()]), idle_timeout_secs=None
        )
        self._runner = WorkerRunner(handle_sigint=False)
        self._running: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._runner.add_workers(self.worker)
        self._running = asyncio.create_task(self._runner.run())

    async def stop(self) -> None:
        await self.worker.queue_frame(EndFrame())
        if self._running is not None:
            await self._running

    async def ask(
        self,
        messages: list[Any],
        *,
        reset: bool,
        on_text: OnText | None = None,
        on_tool: OnTool | None = None,
        on_say: OnText | None = None,
    ) -> dict[str, Any]:
        """One turn. reset=True replaces the conversation with `messages`, else appends them.
        `on_tool` gets the tool's name and whether the model's `say` was spoken, whenever a call
        starts; `on_say` (else `on_text`) gets the `say` sentence."""
        self.tap.begin(on_text, on_tool, on_say)
        frame_type = LLMMessagesUpdateFrame if reset else LLMMessagesAppendFrame
        await self.worker.queue_frame(frame_type(messages=messages, run_llm=True))
        try:
            await asyncio.wait_for(self.tap.done.wait(), TURN_TIMEOUT_S)
            status = "ok"
        except TimeoutError:
            status = "error"
        return {
            "status": status,
            "text": "".join(self.tap.text),
            "first_call_s": self.tap.first_call,
            "first_text_s": self.tap.first_text,
            "end_s": time.perf_counter() - self.tap.started,
        }

    async def cancel(self) -> None:
        """Interrupt the turn in progress and stop forwarding its text."""
        self.tap.on_text = None
        await self.worker.queue_frame(InterruptionFrame())
        self.tap.done.set()
