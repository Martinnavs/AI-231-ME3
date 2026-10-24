# Ticket 02: agent side of fillers: the tools' `say` sentence and the tool event (R2)

**Status:** ready
**Date:** 2026-10-24
**Blocked by:** ticket 01 (both edit `agent_server.py` and `test_agent_server.py`; the OLD blocks below are written against ticket 01's result)

Every tool gets an optional `say` argument: a short sentence the model writes while it calls the tool ("Checking the weather in Baguio."). It is spoken the moment the call starts and never reaches the tool. When the model leaves it out, the agent server sends `{"event": "tool", "name": ...}` so the client can play that tool's pre-recorded line (ticket 03). The system prompt gets one line asking for `say`.

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `pipeline/src/voice_pipeline/agent.py` | edit (list below) | **166** (now 143) |
| `pipeline/src/voice_pipeline/agent_tools.py` | edit (list below) | **129** (now 122) |
| `pipeline/src/voice_pipeline/agent_server.py` | edit (list below) | **158** (now 152) |
| `pipeline/config/eval/system_agent.txt` | edit (list below) | **29** (now 27) |
| `pipeline/tests/test_agent_server.py` | edit (list below) | **148** (now 146) |
| `pipeline/tests/test_say.py` | new, verbatim | **65** |

**Non-goals / touch nothing else:** no change to the root project (ticket 03), `fast_path.py`, either project's `pyproject.toml`/`uv.lock`; never start the agent server or run the dry run (paid API).

**Security-sensitive:** no secrets; `say` is model text, spoken only, never passed to a tool.

## Edits

Apply in this order. Each OLD block occurs exactly once in the file as it is at that moment; replace it with NEW, byte-for-byte.

### Edit 1: `pipeline/src/voice_pipeline/agent.py`

OLD:

````python

    def begin(self, on_text: OnText | None) -> None:
        self.done.clear()
        self.on_text = on_text
        self.text: list[str] = []
````

NEW:

````python

    def begin(self, on_text: OnText | None, on_tool: OnText | None = None) -> None:
        self.done.clear()
        self.on_text = on_text
        self.on_tool = on_tool
        self.announced = False  # the first tool call of a turn is announced once
        self.text: list[str] = []
````

### Edit 2: `pipeline/src/voice_pipeline/agent.py`

OLD:

````python
                self.first_call = now
        elif isinstance(frame, LLMTextFrame):
````

NEW:

````python
                self.first_call = now
            if not self.announced:
                self.announced = True
                await self._announce(frame)
        elif isinstance(frame, LLMTextFrame):
````

### Edit 3: `pipeline/src/voice_pipeline/agent.py`

OLD:

````python
        await self.push_frame(frame, direction)
````

NEW:

````python
        await self.push_frame(frame, direction)

    async def _announce(self, frame: FunctionCallsStartedFrame) -> None:
        """Say the model's `say` sentence for the call, or tell the client which tool runs."""
        calls = list(frame.function_calls)
        say = next((str(c.arguments.get("say") or "").strip() for c in calls), "")
        if say and self.on_text is not None:
            self.text.append(say + " ")
            if self.first_text is None:
                self.first_text = time.perf_counter() - self.started
            await self.on_text(say + " ")
        elif calls and self.on_tool is not None:
            await self.on_tool(calls[0].function_name)
````

### Edit 4: `pipeline/src/voice_pipeline/agent.py`

OLD:

````python
    async def ask(
        self, messages: list[Any], *, reset: bool, on_text: OnText | None = None
    ) -> dict[str, Any]:
        """One turn. reset=True replaces the conversation with `messages`, else appends them."""
        self.tap.begin(on_text)
        frame_type = LLMMessagesUpdateFrame if reset else LLMMessagesAppendFrame
````

NEW:

````python
    async def ask(
        self,
        messages: list[Any],
        *,
        reset: bool,
        on_text: OnText | None = None,
        on_tool: OnText | None = None,
    ) -> dict[str, Any]:
        """One turn. reset=True replaces the conversation with `messages`, else appends them.
        `on_tool` gets the tool's name when a call starts and the model gave no `say`."""
        self.tap.begin(on_text, on_tool)
        frame_type = LLMMessagesUpdateFrame if reset else LLMMessagesAppendFrame
````

### Edit 5: `pipeline/src/voice_pipeline/agent_tools.py`

OLD:

````python
PI_URL = "http://169.254.230.219:8790/mcp"
````

NEW:

````python
PI_URL = "http://169.254.230.219:8790/mcp"
SAY = {
    "type": "string",
    "description": "One short sentence said to the user the moment this call starts, saying "
    "what you are doing (like 'Checking the weather.'); never the result.",
}
````

### Edit 6: `pipeline/src/voice_pipeline/agent_tools.py`

OLD:

````python
    async def handler(params: FunctionCallParams) -> None:
        record.append({"tool": schema.name, "args": dict(params.arguments)})
        await inner(params)
````

NEW:

````python
    async def handler(params: FunctionCallParams) -> None:
        args = {k: v for k, v in params.arguments.items() if k != "say"}
        record.append({"tool": schema.name, "args": args})
        params.arguments = args  # the real tool never sees `say`
        await inner(params)
````

### Edit 7: `pipeline/src/voice_pipeline/agent_tools.py`

OLD:

````python
        description=schema.description,
        properties=schema.properties,
        required=schema.required,
````

NEW:

````python
        description=schema.description,
        properties={**schema.properties, "say": SAY},
        required=schema.required,
````

### Edit 8: `pipeline/src/voice_pipeline/agent_tools.py`

OLD:

````python
        description=SCHEMA.description,
        properties=SCHEMA.properties,
        required=SCHEMA.required,
````

NEW:

````python
        description=SCHEMA.description,
        properties={**SCHEMA.properties, "say": SAY},
        required=SCHEMA.required,
````

### Edit 9: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python

        async with self.lock:
````

NEW:

````python

        async def on_tool(name: str) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"event": "tool", "name": name}))

        async with self.lock:
````

### Edit 10: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
            else:
                turn = asyncio.create_task(self.agent.ask(messages, reset=True, on_text=on_text))
            closed = asyncio.create_task(ws.wait_closed())
````

NEW:

````python
            else:
                turn = asyncio.create_task(
                    self.agent.ask(messages, reset=True, on_text=on_text, on_tool=on_tool)
                )
            closed = asyncio.create_task(ws.wait_closed())
````

### Edit 11: `pipeline/config/eval/system_agent.txt`

OLD:

````text

A turn can need more than one tool, for example a home command and a weather question together; make all the calls. The tool calls are never part of what you say: do not write function names, JSON or tags in your reply, and do not claim an action happened before the tool result comes back. After the results, answer in one to three short sentences.
````

NEW:

````text

When you call a tool, also fill its `say` argument with one short sentence that tells the user what you are doing ("Checking the weather.", "Turning on the lights."); it is spoken the moment the call starts. Never put the result in it.

A turn can need more than one tool, for example a home command and a weather question together; make all the calls. The tool calls are never part of what you say: do not write function names, JSON or tags in your reply, and do not claim an action happened before the tool result comes back. After the results, answer in one to three short sentences.
````

### Edit 12: `pipeline/tests/test_agent_server.py`

OLD:

````python

    async def ask(self, messages: list[dict[str, Any]], *, reset: bool, on_text: Any) -> dict:
        self.asked.append(messages)
````

NEW:

````python

    async def ask(
        self, messages: list[dict[str, Any]], *, reset: bool, on_text: Any, on_tool: Any = None
    ) -> dict:
        self.asked.append(messages)
````

## New files, verbatim

Create each file with exactly this content (byte-for-byte, final newline included).

### `pipeline/tests/test_say.py`

````python
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
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `cd pipeline && uv run --offline ruff check src tests` | All checks passed! |
| 2 | `cd pipeline && uv run --offline ruff format --check src tests` | 15 files already formatted |
| 3 | `cd pipeline && uv run --offline mypy` | Success: no issues found in 9 source files |
| 4 | `cd pipeline && uv run --offline pytest -q -p no:cacheprovider tests/test_say.py tests/test_agent_server.py` | 8 passed |
| 5 | `cd pipeline && uv run --offline pytest -q -p no:cacheprovider` | 61 passed |

## Deviations

(none yet)

## Execution Log

