"""The agent's tools: smart_home, plus the tools of its MCP servers, each call recorded.

MCP servers: docs (a sample: BM25 over the McCarthy corpus), the official time server, web_info
(weather and Wikipedia, in the root project) and the Pi's device server over the network. The
Pi's is optional: without a token, or when the Pi is unreachable, the agent starts without it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters
from mcp.client.session_group import StreamableHttpParameters
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.mcp_service import MCPClient

from voice_pipeline import question
from voice_pipeline.device_say import device_sentence, single_question
from voice_pipeline.tools.smart_home import SCHEMA, make_handler

REPO_ROOT = Path(__file__).resolve().parents[3]
ROOT_MODULE = ["uv", "run", "--group", "laptop", "python", "-m"]
DOCS_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.docs"]
WEB_INFO_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.web_info"]
TAVILY_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.tavily_search"]
DELEGATE_COMMAND = [*ROOT_MODULE, "voice_agent.mcp_servers.delegate"]
TIME_COMMAND = [sys.executable, "-m", "mcp_server_time", "--local-timezone", "Asia/Manila"]
PI_URL_FALLBACK = "http://169.254.230.219:8790/mcp"


def pi_url_from_env(env: Mapping[str, str] | None = None) -> str:
    """The Pi's device-tools URL: $PI_DEVICE_URL (scripts/stack/up.sh sets it from config/pi.env),
    else the address this was first written with."""
    return (os.environ if env is None else env).get("PI_DEVICE_URL") or PI_URL_FALLBACK


PI_URL = pi_url_from_env()
# Tools whose result is already a sentence to say: no second LLM request (R5; TurnTap decides).
SPOKEN_RESULT_TOOLS = {"weather", "wiki_summary", "web_answer", "get_volume", "set_volume"}
# a failure sentence is not an answer: the model gets it and recovers (another topic, its own
# knowledge), as before R5
FAILURE = re.compile(r"couldn't find|did not answer|not connected|can't say|error", re.IGNORECASE)
# failures that may clear on their own (a network blip), unlike "couldn't find": worth one retry
TRANSIENT = re.compile(r"did not answer|not connected|can't say|error", re.IGNORECASE)
NO_SECOND_REQUEST = FunctionCallResultProperties(run_llm=False)
SAY = {
    "type": "string",
    "description": "One short sentence said to the user the moment this call starts, saying "
    "what you are doing (like 'Checking the weather.'); never the result.",
}


# With the model's own Google Search the lookup tools are redundant, and a model given both would
# call the slower one: the server keeps them (the router runs web_answer), the model does not
# see them
BUILTIN_SEARCH_REPLACES = frozenset({"web_answer", "wiki_summary"})


def with_builtin_search(tools: ToolsSchema) -> ToolsSchema:
    """`tools` for a model that searches the web itself: no web_answer or wiki_summary, and
    Gemini's built-in `google_search` next to the function tools (the request then also needs
    tool_config.include_server_side_tool_invocations, see agent.build_llm)."""
    kept = [t for t in tools.standard_tools if t.name not in BUILTIN_SEARCH_REPLACES]
    return ToolsSchema(
        standard_tools=kept, custom_tools={AdapterType.GEMINI: [{"google_search": {}}]}
    )


# Offered to the model only once a delegated job exists (--hide-idle-delegate, off by default):
# before that they only cost schema tokens on every request.
IDLE_DELEGATE_TOOLS = frozenset({"delegate_status", "delegate_cancel"})


def without_tools(tools: ToolsSchema, names: frozenset[str]) -> ToolsSchema:
    """`tools` minus the function tools called `names`."""
    kept = [t for t in tools.standard_tools if t.name not in names]
    return ToolsSchema(standard_tools=kept, custom_tools=tools.custom_tools)


def has_delegate_jobs(jobs_file: Path) -> bool:
    """Whether the delegate server's job table lists any job (an unreadable table counts as yes)."""
    try:
        return bool(json.loads(jobs_file.read_text())["jobs"])
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError):
        return True


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
    """(required, optional) servers: docs, time, web_info, tavily_search; delegate and the Pi's
    (if reachable) are optional."""
    pi = pi_device_server(pi_url)
    required = [
        docs_server(),
        stdio_server(TIME_COMMAND),
        stdio_server(WEB_INFO_COMMAND),
        stdio_server(TAVILY_COMMAND),
    ]
    optional = [stdio_server(DELEGATE_COMMAND)]
    return required, optional + ([pi] if pi is not None else [])


def _recorded(schema: FunctionSchema, record: list[dict[str, Any]]) -> FunctionSchema:
    inner = schema.handler
    assert inner is not None

    async def handler(params: FunctionCallParams) -> None:
        args = {k: v for k, v in params.arguments.items() if k != "say"}
        record.append({"tool": schema.name, "args": args})
        params.arguments = args  # the real tool never sees `say`
        if schema.name == "device_status" and params.tool_call_id != "router":
            # the router path words the status itself (agent_server.run_routed)
            deliver_status = params.result_callback

            async def status_callback(result: Any, *, properties: Any = None) -> None:
                said = (
                    device_sentence(result, question.current)
                    if single_question(question.current)
                    else None
                )
                if said is None:  # nothing usable, or more than a status was asked: the model
                    await deliver_status(result, properties=properties)
                else:
                    await deliver_status(said, properties=properties or NO_SECOND_REQUEST)

            params.result_callback = status_callback
        elif schema.name in SPOKEN_RESULT_TOOLS:
            deliver = params.result_callback

            async def result_callback(result: Any, *, properties: Any = None) -> None:
                failed = isinstance(result, str) and FAILURE.search(result) is not None
                # a failure, or a question that wants a decision made from the result ("should I
                # bring an umbrella"), goes back to the model instead of being spoken as it is
                ask_again = failed or question.needs_reasoning(question.current)
                await deliver(
                    result, properties=properties or (None if ask_again else NO_SECOND_REQUEST)
                )

            params.result_callback = result_callback
        await inner(params)

    return FunctionSchema(
        name=schema.name,
        description=schema.description,
        properties={**schema.properties, "say": SAY},
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
        properties={**SCHEMA.properties, "say": SAY},
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
