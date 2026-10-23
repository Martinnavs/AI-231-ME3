"""The agent's tools: smart_home, plus every tool of the docs MCP server, each call recorded."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp import StdioServerParameters
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.mcp_service import MCPClient

from voice_pipeline.tools.smart_home import SCHEMA, make_handler

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_COMMAND = ["uv", "run", "--group", "laptop", "python", "-m", "voice_agent.mcp_servers.docs"]


def docs_server(command: list[str] = DOCS_COMMAND, env: dict[str, str] | None = None) -> MCPClient:
    """MCP client for the docs server (root project), started over stdio from the repo root."""
    params = StdioServerParameters(
        command=command[0], args=command[1:], cwd=str(REPO_ROOT), env=env
    )
    return MCPClient(server_params=params)


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
    record: list[dict[str, Any]], *, live: str | None = None, docs: MCPClient | None = None
) -> ToolsSchema:
    """smart_home (posting to the ME2 dashboard only when `live` is set) and the docs tools."""
    home = FunctionSchema(
        name=SCHEMA.name,
        description=SCHEMA.description,
        properties=SCHEMA.properties,
        required=SCHEMA.required,
        handler=make_handler(base_url=live, record=record),
    )
    tools: list[FunctionSchema] = [home]
    if docs is not None:
        tools += [_recorded(s, record) for s in (await docs.tools()).standard_tools]
    return ToolsSchema(standard_tools=tools)
