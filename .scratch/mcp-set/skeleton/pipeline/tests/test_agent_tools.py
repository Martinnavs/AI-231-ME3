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
