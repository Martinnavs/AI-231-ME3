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
