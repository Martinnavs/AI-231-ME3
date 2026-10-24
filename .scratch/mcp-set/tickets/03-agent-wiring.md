# Ticket 03: agent wiring for the real MCP tools

**Status:** delivered-for-review
**Date:** 2026-10-24
**Blocked by:** tickets 01 and 02 (landed: `src/voice_agent/mcp_servers/web_info.py`, `scripts/pi_device_mcp.py`); `mcp-server-time` is already in `pipeline/pyproject.toml` and `pipeline/uv.lock` (added by the assistant with the owner's yes; do not touch them)

The agent gets its real tools: the official time server (stdio), the web_info server (stdio, root project) and the Pi's device server (streamable HTTP with `LAN_SPEECH_TOKEN`; optional, so the agent still starts when the Pi is off). TIME and WEATHER leave `smart_home` (the owner, 2026-10-24: the requirement is the ME2 phrases, not ME2's structure); the routing prompt sends time, weather and general knowledge to the real tools, speaker volume with a number and Pi status to the Pi, relative music volume stays `smart_home`. The dry run skips prompts whose tool is not available. Checked by the assistant on a scratch copy: the real servers start and list their tools; the dry run results are in the plan.

Read `GUARDRAILS.md` (this folder) first; it binds this ticket. All commands in this ticket run from `pipeline/` (cd there first).

## Files

| File | Action | Line cap |
|---|---|---|
| `pipeline/src/voice_pipeline/tools/smart_home.py` | edit (list below) | **143** (now 142; exactly 143 after) |
| `pipeline/src/voice_pipeline/agent_server.py` | edit (list below) | **106** (now 103; exactly 106 after) |
| `pipeline/src/voice_pipeline/dryrun.py` | edit (list below) | **175** (now 165; exactly 175 after) |
| `pipeline/config/eval/agent_prompts.jsonl` | edit + append (below) | **87** (now 72; exactly 87 after) |
| `pipeline/src/voice_pipeline/agent_tools.py` | replace whole file, verbatim | **122** |
| `pipeline/config/eval/system_agent.txt` | replace whole file, verbatim | **27** |
| `pipeline/tests/test_agent_tools.py` | new, verbatim | **117** |

**Non-goals / touch nothing else:** no change to the root project, `pyproject.toml` or `uv.lock` of either project, `agent.py`, the doc_chat client; nothing is deployed to the Pi; never start the agent server or run the dry run (it calls the paid Gemini API).

**Security-sensitive:** the Pi client sends the bearer token from `LAN_SPEECH_TOKEN`; copy exactly, never print or log the token.

## Edits

Apply in this order. Each OLD block occurs exactly once in the file as it is at that moment; replace it with NEW, byte-for-byte.

### Edit 1: `pipeline/src/voice_pipeline/tools/smart_home.py`

OLD:

````python
"""One LLM tool for the 19 ME2 home commands, sent to the ME2 dashboard's POST /api/command.
````

NEW:

````python
"""One LLM tool for 17 ME2 home commands, sent to the ME2 dashboard's POST /api/command.

Time and weather are not home commands any more: the agent answers them with the real
time and weather MCP tools (owner, 2026-10-24).
````

### Edit 2: `pipeline/src/voice_pipeline/tools/smart_home.py`

OLD:

````python
    "TEMPERATURE",
    "WEATHER",
    "TIME",
    "TIMER",
````

NEW:

````python
    "TEMPERATURE",
    "TIMER",
````

### Edit 3: `pipeline/src/voice_pipeline/tools/smart_home.py`

OLD:

````python
"timer, alarm, reminders, phone call, message, time, weather. One call per command. "
````

NEW:

````python
"timer, alarm, reminders, phone call, message. One call per command. "
````

### Edit 4: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
    uv run python -m voice_pipeline.agent_server [--port 8781] [--live URL]
````

NEW:

````python
    LAN_SPEECH_TOKEN=... uv run python -m voice_pipeline.agent_server [--port 8781]
        [--live URL] [--pi-url URL]
````

### Edit 5: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
from voice_pipeline.agent_tools import build_tools, docs_server
````

NEW:

````python
from voice_pipeline.agent_tools import PI_URL, build_tools, standard_servers
````

### Edit 6: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
async def run(port: int, live: str | None, system_file: Path) -> int:
    record: list[dict[str, Any]] = []
    tools = await build_tools(record, live=live, docs=docs_server())
````

NEW:

````python
async def run(port: int, live: str | None, system_file: Path, pi_url: str | None) -> int:
    record: list[dict[str, Any]] = []
    servers, optional = standard_servers(pi_url)
    tools = await build_tools(record, live=live, servers=servers, optional=optional)
````

### Edit 7: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.port, args.live, args.system))
````

NEW:

````python
    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.port, args.live, args.system, args.pi_url or None))
````

### Edit 8: `pipeline/src/voice_pipeline/dryrun.py`

OLD:

````python
"""Stress dry run of the agent (native Gemini, smart_home + docs MCP) over a fixed prompt set.
````

NEW:

````python
"""Stress dry run of the agent (native Gemini, smart_home + MCP tools) over a fixed prompt set.
````

### Edit 9: `pipeline/src/voice_pipeline/dryrun.py`

OLD:

````python
    uv run python -m voice_pipeline.dryrun [--limit N] [--live URL] [--run-id ID]
````

NEW:

````python
    LAN_SPEECH_TOKEN=... uv run python -m voice_pipeline.dryrun [--limit N] [--live URL]
        [--run-id ID] [--pi-url URL]

Prompts that expect a tool the agent does not have (the Pi's, when it is off) are skipped
and listed.
````

### Edit 10: `pipeline/src/voice_pipeline/dryrun.py`

OLD:

````python
from voice_pipeline.agent_tools import build_tools, docs_server
````

NEW:

````python
from voice_pipeline.agent_tools import PI_URL, build_tools, runnable, standard_servers
````

### Edit 11: `pipeline/src/voice_pipeline/dryrun.py`

OLD:

````python
async def run(limit: int | None, live: str | None, run_id: str, system_file: Path) -> int:
    lines = (ROOT / "config/eval/agent_prompts.jsonl").read_text().splitlines()
    prompts = [json.loads(line) for line in lines if line.strip()][:limit]
    out = ROOT / "out/agent_dryrun" / run_id
    out.mkdir(parents=True, exist_ok=True)
    record: list[dict[str, Any]] = []
    agent = Agent(
        build_llm(system_file.read_text()), await build_tools(record, live=live, docs=docs_server())
    )
````

NEW:

````python
async def run(
    limit: int | None, live: str | None, run_id: str, system_file: Path, pi_url: str | None
) -> int:
    lines = (ROOT / "config/eval/agent_prompts.jsonl").read_text().splitlines()
    prompts = [json.loads(line) for line in lines if line.strip()][:limit]
    out = ROOT / "out/agent_dryrun" / run_id
    out.mkdir(parents=True, exist_ok=True)
    record: list[dict[str, Any]] = []
    servers, optional = standard_servers(pi_url)
    tools = await build_tools(record, live=live, servers=servers, optional=optional)
    prompts, skipped = runnable(prompts, {t.name for t in tools.standard_tools})
    if skipped:
        print(f"skipped (tool not available): {', '.join(skipped)}")
    agent = Agent(build_llm(system_file.read_text()), tools)
````

### Edit 12: `pipeline/src/voice_pipeline/dryrun.py`

OLD:

````python
    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.limit, args.live, args.run_id, args.system))
````

NEW:

````python
    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.limit, args.live, args.run_id, args.system, args.pi_url or None))
````

### Edit 13: `pipeline/config/eval/agent_prompts.jsonl`

OLD:

````json
{"id": "c12", "kind": "canonical", "turns": ["What's the weather?"], "expect": [[{"intent": "WEATHER", "slot": null}]]}
````

NEW:

````json
{"id": "c12", "kind": "canonical", "turns": ["What's the weather?"], "expect": [[{"tool": "weather"}]]}
````

### Edit 14: `pipeline/config/eval/agent_prompts.jsonl`

OLD:

````json
{"id": "c13", "kind": "canonical", "turns": ["What time is it?"], "expect": [[{"intent": "TIME", "slot": null}]]}
````

NEW:

````json
{"id": "c13", "kind": "canonical", "turns": ["What time is it?"], "expect": [[{"tool": "get_current_time"}]]}
````

### Edit 15: `pipeline/config/eval/agent_prompts.jsonl`

OLD:

````json
{"id": "p05", "kind": "paraphrase", "turns": ["Do I need an umbrella today?"], "expect": [[{"intent": "WEATHER", "slot": null}]]}
````

NEW:

````json
{"id": "p05", "kind": "paraphrase", "turns": ["Do I need an umbrella today?"], "expect": [[{"tool": "weather"}]]}
````

### Edit 16: `pipeline/config/eval/agent_prompts.jsonl`

OLD:

````json
{"id": "p06", "kind": "paraphrase", "turns": ["Got the time?"], "expect": [[{"intent": "TIME", "slot": null}]]}
````

NEW:

````json
{"id": "p06", "kind": "paraphrase", "turns": ["Got the time?"], "expect": [[{"tool": "get_current_time"}]]}
````

### Append to the end of `pipeline/config/eval/agent_prompts.jsonl`

After the edits, add these lines at the end of the file (one JSON object per line, the file ending with a newline):

````json
{"id": "t01", "kind": "time", "turns": ["What time is it in London?"], "expect": [[{"tool": "get_current_time"}]]}
{"id": "t02", "kind": "time", "turns": ["What's today's date?"], "expect": [[{"tool": "get_current_time"}]]}
{"id": "t03", "kind": "time", "turns": ["If it's 3 PM here, what time is it in New York?"], "expect": [[{"tool": "convert_time"}]]}
{"id": "w01", "kind": "weather", "turns": ["Will it rain tomorrow?"], "expect": [[{"tool": "weather"}]]}
{"id": "w02", "kind": "weather", "turns": ["What's the weather like in Baguio?"], "expect": [[{"tool": "weather"}]]}
{"id": "w03", "kind": "weather", "turns": ["How hot will it get today?"], "expect": [[{"tool": "weather"}]]}
{"id": "k01", "kind": "knowledge", "turns": ["Who is Lionel Messi?"], "expect": [[{"tool": "wiki_summary"}]]}
{"id": "k02", "kind": "knowledge", "turns": ["Tell me about the Eiffel Tower."], "expect": [[{"tool": "wiki_summary"}]]}
{"id": "k03", "kind": "knowledge", "turns": ["What is a Raspberry Pi?"], "expect": [[{"tool": "wiki_summary"}]]}
{"id": "v01", "kind": "device", "turns": ["Set the speaker volume to 40 percent."], "expect": [[{"tool": "set_volume"}]]}
{"id": "v02", "kind": "device", "turns": ["How loud is the speaker right now?"], "expect": [[{"tool": "get_volume"}]]}
{"id": "v03", "kind": "device", "turns": ["Put the speaker at 20 percent."], "expect": [[{"tool": "set_volume"}]]}
{"id": "s01", "kind": "device", "turns": ["How hot is the Pi running?"], "expect": [[{"tool": "device_status"}]]}
{"id": "s02", "kind": "device", "turns": ["Is the speaker connected?"], "expect": [[{"tool": "device_status"}]]}
{"id": "m01", "kind": "mixed", "turns": ["Turn on the lights and tell me the weather."], "expect": [[{"intent": "LIGHT_ON", "slot": null}, {"tool": "weather"}]]}
````

## Whole files, verbatim

Replace each existing file entirely with this content, and create the new one (byte-for-byte, final newline included).

### `pipeline/src/voice_pipeline/agent_tools.py`

````python
"""The agent's tools: smart_home, plus the tools of its MCP servers, each call recorded.

MCP servers: docs (a sample: BM25 over the McCarthy corpus), the official time server, web_info
(weather and Wikipedia, in the root project) and the Pi's device server over the network. The
Pi's is optional: without a token, or when the Pi is unreachable, the agent starts without it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters
from mcp.client.session_group import StreamableHttpParameters
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.mcp_service import MCPClient

from voice_pipeline.tools.smart_home import SCHEMA, make_handler

REPO_ROOT = Path(__file__).resolve().parents[3]
ROOT_MODULE = ["uv", "run", "--group", "laptop", "python", "-m"]
DOCS_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.docs"]
WEB_INFO_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.web_info"]
TIME_COMMAND = [sys.executable, "-m", "mcp_server_time", "--local-timezone", "Asia/Manila"]
PI_URL = "http://169.254.230.219:8790/mcp"


def stdio_server(command: list[str], env: dict[str, str] | None = None) -> MCPClient:
    """MCP client for a server started over stdio from the repo root."""
    params = StdioServerParameters(
        command=command[0], args=command[1:], cwd=str(REPO_ROOT), env=env
    )
    return MCPClient(server_params=params)


def docs_server(command: list[str] = DOCS_COMMAND, env: dict[str, str] | None = None) -> MCPClient:
    """MCP client for the docs server (root project)."""
    return stdio_server(command, env)


def pi_device_server(url: str | None = PI_URL, token: str | None = None) -> MCPClient | None:
    """MCP client for the Pi's device server (token: LAN_SPEECH_TOKEN), or None without one."""
    token = os.environ.get("LAN_SPEECH_TOKEN") if token is None else token
    if not url or not token:
        return None
    params = StreamableHttpParameters(url=url, headers={"Authorization": f"Bearer {token}"})
    return MCPClient(server_params=params)


def standard_servers(pi_url: str | None = PI_URL) -> tuple[list[MCPClient], list[MCPClient]]:
    """(required, optional) servers: docs, time and web_info; the Pi's when it can be reached."""
    pi = pi_device_server(pi_url)
    required = [docs_server(), stdio_server(TIME_COMMAND), stdio_server(WEB_INFO_COMMAND)]
    return required, [pi] if pi is not None else []


def _recorded(schema: FunctionSchema, record: list[dict[str, Any]]) -> FunctionSchema:
    inner = schema.handler
    assert inner is not None

    async def handler(params: FunctionCallParams) -> None:
        record.append({"tool": schema.name, "args": dict(params.arguments)})
        await inner(params)

    return FunctionSchema(
        name=schema.name,
        description=schema.description,
        properties=schema.properties,
        required=schema.required,
        handler=handler,
    )


async def build_tools(
    record: list[dict[str, Any]],
    *,
    live: str | None = None,
    servers: Sequence[MCPClient] = (),
    optional: Sequence[MCPClient] = (),
) -> ToolsSchema:
    """smart_home (posting to the ME2 dashboard only when `live` is set) and the MCP tools.

    A server in `optional` that cannot be reached is left out with a warning; a server in
    `servers` that cannot be reached is an error."""
    home = FunctionSchema(
        name=SCHEMA.name,
        description=SCHEMA.description,
        properties=SCHEMA.properties,
        required=SCHEMA.required,
        handler=make_handler(base_url=live, record=record),
    )
    tools: list[FunctionSchema] = [home]
    for server in servers:
        tools += [_recorded(s, record) for s in (await server.tools()).standard_tools]
    for server in optional:
        try:
            schemas = (await server.tools()).standard_tools
        except Exception as exc:  # the Pi is off or unreachable: start without its tools
            print(f"MCP server left out ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue
        tools += [_recorded(s, record) for s in schemas]
    return ToolsSchema(standard_tools=tools)


def runnable(
    prompts: list[dict[str, Any]], names: set[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """(prompts whose expected tools are all available, ids of the ones that are not)."""
    kept: list[dict[str, Any]] = []
    skipped: list[str] = []
    for prompt in prompts:
        needed = {e["tool"] for turn in prompt["expect"] for e in turn if "tool" in e}
        if needed <= names:
            kept.append(prompt)
        else:
            skipped.append(str(prompt["id"]))
    return kept, skipped
````

### `pipeline/config/eval/system_agent.txt`

````text
You are a friendly home assistant in a voice conversation. Your responses will be spoken aloud, so avoid emojis, bullet points, code, or other formatting that can't be spoken.

For every user turn, decide silently which case it is, then act:

Case A, a home command. Call the smart_home tool, once per command. Intents:
- Music: PLAY_MUSIC, PAUSE, STOP, NEXT (skip), VOLUME_UP, VOLUME_DOWN (turning the music or the volume up or down without a number, "it's too loud").
- Lights: LIGHT_ON, LIGHT_OFF, BRIGHTNESS (slot like "40 percent"), COLOR (slot like "purple").
- Climate: TEMPERATURE (slot like "24 degrees").
- Timers and alarms: TIMER (slot like "45 minutes"), ALARM (slot like "6:30 AM").
- Reminders: CREATE_REMINDER (slot is what to be reminded of, in the user's words), LIST_REMINDERS.
- Phone: CALL, MESSAGE (slot is a contact name from the phonebook in the tool description; match relations like "my sister" to the contact's name).
Convert spoken numbers and times to written form ("ten at night" is "10 PM", "twenty degrees" is "20 degrees").

Case B, the time, the weather, or general knowledge. Use the real tools and say what they return:
- The time or the date: get_current_time with an IANA time zone ("Asia/Manila" when the user names no place, "Europe/London" for London). Converting a time between places: convert_time.
- The weather, rain, temperature outside: weather (leave the place empty for home; day is "today" or "tomorrow").
- A person, place, book, film or thing in general: wiki_summary with the subject's name.

Case C, a question about the documents' topic (Cormac McCarthy, his books, characters, adaptations, life). Call search_docs with a short self-contained question, then answer from the passages in two or three sentences. If the passages do not say, say you don't know.

Case D, the speaker or the Raspberry Pi itself. A speaker volume with a number, or asking how loud the speaker is: set_volume or get_volume. The Pi's temperature, memory, uptime, or whether the speaker is connected: device_status. If these tools are not available, say the Pi cannot be reached.

Case E, a home command missing its value (a countdown with no length, "make it warmer" with no number, a call or message to someone not in the phonebook). Ask one short question, or say the person is not in the phonebook. Do not call the tool and do not guess.

Case F, anything else. Answer briefly without tools.

A turn can need more than one tool, for example a home command and a weather question together; make all the calls. The tool calls are never part of what you say: do not write function names, JSON or tags in your reply, and do not claim an action happened before the tool result comes back. After the results, answer in one to three short sentences.
````

### `pipeline/tests/test_agent_tools.py`

````python
"""agent_tools: the MCP servers the agent starts with, and what happens when one is missing."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from voice_pipeline.agent_tools import (
    TIME_COMMAND,
    build_tools,
    pi_device_server,
    runnable,
    standard_servers,
)
from voice_pipeline.tools.smart_home import INTENTS

PROMPTS = Path(__file__).resolve().parents[1] / "config" / "eval" / "agent_prompts.jsonl"
TOOL_NAMES = {
    "search_docs", "get_current_time", "convert_time", "weather", "wiki_summary",
    "get_volume", "set_volume", "device_status",
}  # fmt: skip


class FakeServer:
    """Stands in for an MCPClient: offers `names`, or fails like an unreachable server."""

    def __init__(self, names: list[str], fail: bool = False) -> None:
        self.names = names
        self.fail = fail

    async def tools(self) -> ToolsSchema:
        if self.fail:
            raise httpx.ConnectError("All connection attempts failed")

        async def handler(params: Any) -> None:
            await params.result_callback("ok")

        return ToolsSchema(
            standard_tools=[
                FunctionSchema(name=n, description=n, properties={}, required=[], handler=handler)
                for n in self.names
            ]
        )


def _names(tools: ToolsSchema) -> list[str]:
    return [t.name for t in tools.standard_tools]


async def test_smart_home_then_every_server_s_tools() -> None:
    tools = await build_tools(
        [], servers=[FakeServer(["weather", "wiki_summary"])], optional=[FakeServer(["get_volume"])]
    )
    assert _names(tools) == ["smart_home", "weather", "wiki_summary", "get_volume"]


async def test_an_unreachable_optional_server_is_left_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tools = await build_tools(
        [], servers=[FakeServer(["weather"])], optional=[FakeServer(["get_volume"], fail=True)]
    )
    assert _names(tools) == ["smart_home", "weather"]
    assert "MCP server left out (ConnectError" in capsys.readouterr().err


async def test_an_unreachable_required_server_is_an_error() -> None:
    with pytest.raises(httpx.ConnectError):
        await build_tools([], servers=[FakeServer(["weather"], fail=True)])


def test_the_pi_server_needs_a_url_and_a_token() -> None:
    assert pi_device_server("http://pi:8790/mcp", token="") is None
    assert pi_device_server(None, token="secret") is None
    client = pi_device_server("http://pi:8790/mcp", token="secret")
    assert client is not None
    assert client._server_params.headers == {"Authorization": "Bearer secret"}


def test_standard_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAN_SPEECH_TOKEN", raising=False)
    required, optional = standard_servers()
    assert len(required) == 3 and optional == []  # no token: no Pi
    monkeypatch.setenv("LAN_SPEECH_TOKEN", "secret")
    assert len(standard_servers()[1]) == 1
    assert len(standard_servers(None)[1]) == 0
    assert TIME_COMMAND[1:] == ["-m", "mcp_server_time", "--local-timezone", "Asia/Manila"]


def test_prompts_needing_a_missing_tool_are_skipped() -> None:
    prompts = [
        {"id": "a", "expect": [[{"tool": "weather"}]]},
        {"id": "b", "expect": [[{"tool": "set_volume"}]]},
        {"id": "c", "expect": [[{"intent": "LIGHT_ON", "slot": None}]]},
    ]
    kept, skipped = runnable(prompts, {"smart_home", "weather"})
    assert [p["id"] for p in kept] == ["a", "c"] and skipped == ["b"]


def test_time_and_weather_are_not_home_commands_any_more() -> None:
    assert "TIME" not in INTENTS and "WEATHER" not in INTENTS
    assert len(INTENTS) == 17


def test_every_expected_call_names_a_known_tool_or_intent() -> None:
    for line in PROMPTS.read_text().splitlines():
        prompt = json.loads(line)
        for turn in prompt["expect"]:
            for call in turn:
                if "tool" in call:
                    assert call["tool"] in TOOL_NAMES, prompt["id"]
                else:
                    assert call["intent"] in INTENTS, prompt["id"]
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `cd pipeline && uv run --offline ruff check src tests` | All checks passed! |
| 2 | `cd pipeline && uv run --offline ruff format --check src tests` | 11 files already formatted |
| 3 | `cd pipeline && uv run --offline mypy` | Success: no issues found in 7 source files |
| 4 | `cd pipeline && uv run --offline pytest -q -p no:cacheprovider tests/test_agent_tools.py` | 8 passed |
| 5 | `cd pipeline && uv run --offline pytest -q -p no:cacheprovider` | 24 passed |

## Deviations

none

## Execution Log

### 2026-10-24 agy: DONE
- Proof: 1 ✅ (`cd pipeline && uv run --offline ruff check src tests`: All checks passed!) 2 ✅ (`cd pipeline && uv run --offline ruff format --check src tests`: 11 files already formatted) 3 ✅ (`cd pipeline && uv run --offline mypy`: Success: no issues found in 7 source files) 4 ✅ (`cd pipeline && uv run --offline pytest -q -p no:cacheprovider tests/test_agent_tools.py`: 8 passed in 1.05s) 5 ✅ (`cd pipeline && uv run --offline pytest -q -p no:cacheprovider`: 24 passed in 1.53s)
- Files:
  - `pipeline/src/voice_pipeline/tools/smart_home.py` (143 / 143)
  - `pipeline/src/voice_pipeline/agent_server.py` (106 / 106)
  - `pipeline/src/voice_pipeline/dryrun.py` (175 / 175)
  - `pipeline/config/eval/agent_prompts.jsonl` (87 / 87)
  - `pipeline/src/voice_pipeline/agent_tools.py` (122 / 122)
  - `pipeline/config/eval/system_agent.txt` (27 / 27)
  - `pipeline/tests/test_agent_tools.py` (117 / 117)
- Deviations: none
- Notes for reviewer: none


### 2026-10-24 reviewer (assistant): ACCEPTED
- `diff -rq orig work`: only this ticket's files (and the tickets) changed; `cmp` against the tested reference: byte-identical. Deviations: none.
- Integrated (backup `.scratch/mcp-set/backup-153842/`). Root gate: ruff and mypy clean, pytest 636 passed, 9 skipped, 2 xfailed, 1 failed (the known `test_flow` case). `pipeline/` gate: ruff and mypy clean, pytest 24 passed.
