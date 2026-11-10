"""The agent: one long-lived Pipecat pipeline around native Gemini and the agent's tools.

`Agent.ask` runs one turn and reports its timings; text is streamed to an optional callback.
`Agent.cancel` interrupts the turn in progress. The dry run and the agent server both use it.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMMessagesUpdateFrame,
    LLMTextFrame,
    MetricsFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.google.llm import GoogleLLMService
from pipecat.workers.runner import WorkerRunner

from voice_pipeline import question
from voice_pipeline.agent_tools import DELEGATE_STARTED, FAILURE
from voice_pipeline.device_say import device_sentence

SAY_DELAY_S = 0.6  # a tool that answers within this makes the `say` sentence unnecessary
# Tools that never answer that fast: their `say` is spoken at once (weather took 1.3-4.2 s every
# time in the 2026-10-27 session, so holding its `say` 0.6 s only delayed it).
SAY_DELAY_TODAY = {"weather": 0.0}
# 2026-11-05 logs: first text came exactly one hold after the first call (80 Gemini tool turns,
# median 0.61 s), and these tools took 0.63 s at the fastest (routed web lookups, median 1.9 s)
SAY_DELAY_SLOW = {**SAY_DELAY_TODAY, "web_answer": 0.0, "search_docs": 0.0, "delegate": 0.0}
# web_answer's say is spoken at once: 8 live lookups, first text median 1.34 -> 0.73 s (ticket P)
SAY_DELAY_BY_TOOL = {**SAY_DELAY_TODAY, "web_answer": 0.0}
MODEL = "gemini-3.1-flash-lite"
# A connectivity stall (R4): Pipecat waits `stream_idle_timeout_secs` (20 s) for the first chunk
# and, by default, does not retry; on 2026-11-05 one stalled request ended a turn after 20 s and
# the next took 18 s to call a tool. The first chunk is normally back in under 2 s (p90 1.7 s,
# p99 7.7 s with tools), so: one re-issue after 5 s, then at most 10 s more: 15 s worst case, inside
# the client's 20 s cap, and about 7 s when the retry answers normally (the 8 s no-progress cap).
RETRY_FIRST_CHUNK_S = 5.0
STREAM_IDLE_S = 10.0
TURN_TIMEOUT_S = 20.0
# After a tool result the model's follow-up call normally starts in under a second; one in
# 2026-11-05 (device_status back in 0.15 s) gave no text for 16 s and the client dropped the turn.
STALL_S = 8.0
STALL_BY_TOOL: Mapping[str, float] = {"web_research": 16.0, "search_docs": 10.0}
STALL_GENERIC = "Sorry, I got the result but couldn't put it into words. Could you ask again?"
# Turns are seconds to minutes apart, but the Gemini client lets an idle connection go after 15 s
# (the SDK's aiohttp default) and the server may close sooner: every turn then paid a new TCP+TLS
# handshake. At 15 s idle, 12 interleaved pairs: first chunk p50 1095 -> 835 ms, p90 5.9 -> 1.0 s
# (docs/OPTIMIZATION-RESEARCH.md).
KEEPALIVE_S = 300.0
OnText = Callable[[str], Awaitable[None]]
OnTool = Callable[[str, bool], Awaitable[None]]  # tool name, whether its `say` was spoken


def read_key(secret_dir: str) -> str:
    folder = Path.home() / ".secrets" / secret_dir
    for name in ("api-key.txt", "api_key.txt"):
        path = folder / name
        if path.exists():
            return "".join(path.read_text().split())
    raise SystemExit(f"no key in ~/.secrets/{secret_dir}/")


_sessions: list[Any] = []  # the aiohttp sessions behind the Gemini clients, closed at shutdown


def warm_http_options(keepalive_s: float | None = KEEPALIVE_S, ipv4_only: bool = False) -> Any:
    """HttpOptions whose aiohttp session keeps idle connections `keepalive_s` seconds and, with
    `ipv4_only`, connects over IPv4 only; None (the SDK defaults) when neither is asked for or
    aiohttp is not installed. Call it inside a running event loop: the session belongs to it."""
    if not keepalive_s and not ipv4_only:
        return None
    try:
        import aiohttp
        from google.genai.types import HttpOptions
    except ImportError:
        return None
    options: dict[str, Any] = {"keepalive_timeout": keepalive_s or 15.0}  # 15 s: aiohttp's default
    if ipv4_only:
        options["family"] = socket.AF_INET
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(**options))
    _sessions.append(session)
    return HttpOptions(aiohttp_client=session)


async def close_llm_sessions() -> None:
    """Close the sessions that `warm_http_options` created."""
    while _sessions:
        await _sessions.pop().close()


def build_llm(
    system: str,
    model: str = MODEL,
    keepalive_s: float | None = KEEPALIVE_S,
    ipv4_only: bool = False,
    builtin_search: bool = False,
) -> GoogleLLMService:
    """`builtin_search`: the model may use Gemini's own Google Search next to the function tools,
    which the API only accepts with include_server_side_tool_invocations (HTTP 400 otherwise)."""
    return GoogleLLMService(
        api_key=read_key("google-ai-studio"),
        http_options=warm_http_options(keepalive_s, ipv4_only),
        settings=GoogleLLMService.Settings(model=model, system_instruction=system),
        tool_config={"include_server_side_tool_invocations": True} if builtin_search else None,
        retry_on_timeout=True,
        retry_timeout_secs=RETRY_FIRST_CHUNK_S,
        stream_idle_timeout_secs=STREAM_IDLE_S,
    )


def stall_sentence(name: str, result: Any, asked: str) -> str:
    """What to say when the model stalls after a tool: the Pi status worded for the question, a
    tool's own sentence (web_answer, weather, volume), else an apology."""
    if name in ("web_research", "search_docs"):
        return STALL_GENERIC
    if name == "device_status":
        said = device_sentence(result, asked)
        if said is not None:
            return said
    elif isinstance(result, dict):
        message = result.get("message")
        text = message.strip() if isinstance(message, str) and result.get("ok") is not False else ""
        if text and not FAILURE.search(text):
            return text
    elif (
        isinstance(result, str)
        and result.strip()
        and not result.lstrip().startswith(("{", "["))  # a tool's JSON is not a sentence
        and not FAILURE.search(result)
    ):
        return " ".join(result.split())
    return STALL_GENERIC


class TurnTap(FrameProcessor):
    """Times one turn (first tool call, first text, end) and forwards its text."""

    def __init__(
        self,
        say_delay_s: float = SAY_DELAY_S,
        stall_s: float = STALL_S,
        say_delays: Mapping[str, float] | None = None,
        stall_delays: Mapping[str, float] | None = None,
    ) -> None:
        super().__init__()
        self.say_delay_s = say_delay_s
        self.say_delays = SAY_DELAY_BY_TOOL if say_delays is None else say_delays
        self.stall_s = stall_s
        self.stall_delays = STALL_BY_TOOL if stall_delays is None else stall_delays
        self.interrupt: Callable[[], Awaitable[None]] | None = None  # stops the model's late reply
        self._say_task: asyncio.Task[None] | None = None
        self._stall_task: asyncio.Task[None] | None = None
        self.done = asyncio.Event()
        self.on_text: OnText | None = None
        self.on_say: OnText | None = None
        self.on_tool: OnTool | None = None
        # LLM requests and tokens since the tap was made (not reset per turn); tokens need the
        # worker's usage metrics (Agent(usage=True))
        self.usage = {"requests": 0, "prompt": 0, "completion": 0, "cached": 0}
        self.begin(None)

    def begin(
        self,
        on_text: OnText | None,
        on_tool: OnTool | None = None,
        on_say: OnText | None = None,
    ) -> None:
        self._drop_say()
        self._drop_stall()
        self.stall_fallback: dict[str, Any] | None = None
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
        self.calls_started = 0  # tool calls in this turn's response
        self.results_seen = 0
        self.said = False  # the model gave a `say` sentence for the call (spoken, or still held)
        self.say_text = ""  # ... and this is it, until spoken or dropped
        self.responding = False  # a late end frame from a cancelled turn must not end this one

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        now = time.perf_counter() - self.started
        if isinstance(frame, LLMFullResponseStartFrame):
            self.usage["requests"] += 1
            self.calls_in_response = False
            self.responding = True
        elif isinstance(frame, MetricsFrame):
            for data in frame.data:
                if isinstance(data, LLMUsageMetricsData):
                    tokens = data.value
                    self.usage["prompt"] += tokens.prompt_tokens
                    self.usage["completion"] += tokens.completion_tokens + (
                        tokens.reasoning_tokens or 0
                    )
                    self.usage["cached"] += tokens.cache_read_input_tokens or 0
        elif isinstance(frame, FunctionCallsStartedFrame):
            self.calls_in_response = True
            self.calls_started = len(frame.function_calls)
            self.results_seen = 0
            self._drop_stall()
            if self.first_call is None:
                self.first_call = now
            if not self.announced:
                self.announced = True
                await self._announce(frame)
        elif isinstance(frame, FunctionCallResultFrame):
            await self._result(frame)
        elif isinstance(frame, LLMTextFrame):
            if frame.text.strip():
                self._drop_stall()
            await self._speak_say()  # the model's own words follow the `say`, never precede it
            if self.first_text is None and frame.text.strip():
                self.first_text = now
            self.text.append(frame.text)
            if self.on_text is not None:
                await self.on_text(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame) and self.responding:
            if not self.calls_in_response:
                self._drop_stall()
                self.done.set()
        await self.push_frame(frame, direction)

    async def _announce(self, frame: FunctionCallsStartedFrame) -> None:
        """Hold the model's `say` sentence for the call for `say_delay_s`: a tool that answers
        within that makes it unnecessary (it is dropped and the answer is spoken alone), else it
        goes out as its own event (`on_say`, else `on_text`). Always tell the client which tool
        runs, and whether a `say` is on its way."""
        calls = list(frame.function_calls)
        say = next((str(c.arguments.get("say") or "").strip() for c in calls), "")
        self.said = bool(say and (self.on_say or self.on_text) is not None)
        if self.said:
            self.say_text = say
            delay = self.say_delays.get(calls[0].function_name, self.say_delay_s)
            if delay > 0 and self.say_delay_s > 0:
                self._say_task = asyncio.create_task(self._say_after_delay(delay))
            else:
                await self._speak_say()
        if calls and self.on_tool is not None:
            await self.on_tool(calls[0].function_name, self.said)

    async def _say_after_delay(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._speak_say()

    async def _speak_say(self) -> None:
        """Send the held `say` sentence now (once)."""
        say, self.say_text = self.say_text, ""
        self._drop_say()
        speak = self.on_say or self.on_text
        if not say or speak is None:
            return
        self.text.append(say + " ")
        if self.first_text is None:
            self.first_text = time.perf_counter() - self.started
        await speak(say + " ")

    def _drop_say(self) -> None:
        """Forget a held `say` sentence and stop its timer."""
        self.say_text = ""
        task, self._say_task = self._say_task, None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _drop_stall(self) -> None:
        task, self._stall_task = self._stall_task, None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _arm_stall(self, frame: FunctionCallResultFrame) -> None:
        """Start the watch once every call of the response has returned its result (the model
        only continues then, and a slow second tool must not look like a stalled model)."""
        self.results_seen += 1
        if self.done.is_set() or self.results_seen < self.calls_started:
            return
        self._drop_stall()
        delay = self.stall_delays.get(frame.function_name, self.stall_s)
        self._stall_task = asyncio.create_task(
            self._stall_watch(frame.function_name, frame.result, delay)
        )

    async def _stall_watch(self, name: str, result: Any, wait_s: float | None = None) -> None:
        """The model has had `stall_s` since a tool result and said nothing: speak a sentence
        built from the result, stop the model's late reply and end the turn."""
        delay = self.stall_s if wait_s is None else wait_s
        await asyncio.sleep(delay)
        if self.done.is_set():
            return
        sentence = stall_sentence(name, result, question.current)
        self.stall_fallback = {"tool": name, "after_s": delay, "text": sentence}
        self.text.append(sentence + " ")
        if self.first_text is None:
            self.first_text = time.perf_counter() - self.started
        if self.on_text is not None:
            await self.on_text(sentence + " ")
        self.done.set()
        if self.interrupt is not None:
            await self.interrupt()

    async def _result(self, frame: FunctionCallResultFrame) -> None:
        """A tool asked for no second LLM request (`run_llm=False`): allow it only when the turn
        has one call and something to say without the model: the result when it is a sentence,
        else the model's `say`. Then speak the result and end the turn. Otherwise the model runs
        as usual (this tap sits before the aggregator, so it can still change the request)."""
        props = frame.properties
        if props is None or props.run_llm is not False:
            self._arm_stall(frame)
            return
        sentence = frame.result.strip() if isinstance(frame.result, str) else ""
        if self.calls_started != 1 or not (sentence or self.said):
            frame.properties = replace(props, run_llm=None)
            self._arm_stall(frame)
            return
        if sentence.startswith("Playing ") and self.said and not self.say_text:
            sentence = ""  # the model's `say` already announced the music; once is enough
        if sentence == DELEGATE_STARTED:  # the model's own line first, else say that it started
            if self.say_text:
                await self._speak_say()
            elif not self.said:
                sentence = "Started. " + sentence
        if not sentence:
            await self._speak_say()  # the `say` is the whole reply
        if sentence:
            self._drop_say()  # the answer is here: no need to announce that it is coming
            self.text.append(sentence + " ")
            if self.first_text is None:
                self.first_text = time.perf_counter() - self.started
            if self.on_text is not None:
                await self.on_text(sentence + " ")
        self.done.set()


class Agent:
    """One pipeline, one connection: user aggregator -> LLM -> tap -> assistant aggregator."""

    def __init__(
        self,
        llm: GoogleLLMService,
        tools: ToolsSchema,
        usage: bool = False,
        say_delays: Mapping[str, float] | None = None,
    ) -> None:
        self.all_tools = tools
        self.context = LLMContext(tools=tools)
        pair = LLMContextAggregatorPair(self.context)
        self.tap = TurnTap(say_delays=say_delays)
        # No idle timeout: a voice session can sit quiet for minutes between turns.
        self.worker = PipelineWorker(
            Pipeline([pair.user(), llm, self.tap, pair.assistant()]),
            params=PipelineParams(enable_usage_metrics=usage),
            idle_timeout_secs=None,
        )
        self._runner = WorkerRunner(handle_sigint=False)
        self.tap.interrupt = lambda: self.worker.queue_frame(InterruptionFrame())
        self._running: asyncio.Task[None] | None = None

    def hide(self, names: frozenset[str]) -> None:
        """Offer the model every tool except `names` from the next request on."""
        from voice_pipeline.agent_tools import without_tools

        self.context.set_tools(without_tools(self.all_tools, names) if names else self.all_tools)

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
            **({"stall_fallback": self.tap.stall_fallback} if self.tap.stall_fallback else {}),
        }

    async def cancel(self) -> None:
        """Interrupt the turn in progress and stop forwarding its text."""
        self.tap.on_text = None
        self.tap._drop_say()
        self.tap._drop_stall()
        await self.worker.queue_frame(InterruptionFrame())
        self.tap.done.set()
