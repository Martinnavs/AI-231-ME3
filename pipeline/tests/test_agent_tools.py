"""agent_tools: the MCP servers the agent starts with, and what happens when one is missing."""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema

from voice_pipeline import agent_tools, question
from voice_pipeline.agent_tools import (
    TIME_COMMAND,
    _recorded,
    build_tools,
    pi_device_server,
    runnable,
    same_app_paths,
    standard_servers,
    with_builtin_search,
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
    assert _names(tools) == [
        "smart_home",
        "open_app",
        "weather",
        "wiki_summary",
        "get_volume",
    ]


async def test_an_unreachable_optional_server_is_left_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tools = await build_tools(
        [], servers=[FakeServer(["weather"])], optional=[FakeServer(["get_volume"], fail=True)]
    )
    assert _names(tools) == ["smart_home", "open_app", "weather"]
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
    monkeypatch.setenv("MUSIC_SOURCE", "youtube")  # the Spotify source adds the music server
    required, optional = standard_servers()
    assert len(required) == 4 and len(optional) == 1  # no token: the delegate server, no Pi
    monkeypatch.setenv("LAN_SPEECH_TOKEN", "secret")
    assert len(standard_servers()[1]) == 2
    assert len(standard_servers(None)[1]) == 1
    assert TIME_COMMAND[1:] == ["-m", "mcp_server_time", "--local-timezone", "Asia/Manila"]


def test_the_speech_server_is_optional_and_hidden_from_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_tools, "SPEECH_LAN_URL", None)
    assert agent_tools.speech_command() is None
    monkeypatch.setattr(agent_tools, "SPEECH_LAN_URL", "ws://10.0.0.1:8770")
    command = agent_tools.speech_command()
    assert command is not None and command[-2:] == [
        "--lan-token-file",
        "~/.config/lan_speech_token",
    ]
    assert "--lan-url" in command and "ws://10.0.0.1:8770" in command and "secret" not in command
    assert set(agent_tools.SPEECH_TOOLS) <= agent_tools.MODEL_HIDDEN


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


async def _spoken_result_properties(tool: str, asked: str) -> Any:
    """What `_recorded` tells the pipeline to do after `tool` answers `asked`."""
    seen: list[Any] = []

    async def handler(params: Any) -> None:
        await params.result_callback("Today in Davao: light rain, 40 percent chance of rain.")

    async def result_callback(result: Any, *, properties: Any = None) -> None:
        seen.append(properties)

    schema = FunctionSchema(name=tool, description="d", properties={}, required=[], handler=handler)
    question.current = asked
    try:
        wrapped = _recorded(schema, [])
        assert wrapped.handler is not None
        params = SimpleNamespace(arguments={}, result_callback=result_callback)
        await wrapped.handler(params)  # type: ignore[arg-type]
    finally:
        question.current = ""
    return seen[0]


async def test_a_lookup_is_spoken_as_it_is_for_a_plain_question() -> None:
    properties = await _spoken_result_properties("weather", "What's the weather in Davao?")
    assert properties is not None and properties.run_llm is False


async def test_a_lookup_goes_back_to_the_model_when_the_question_wants_a_decision() -> None:
    properties = await _spoken_result_properties("weather", "Should I bring an umbrella?")
    assert properties is None


def _schema(name: str) -> FunctionSchema:
    return FunctionSchema(name=name, description="d", properties={}, required=[])


def test_with_builtin_search_hides_the_lookup_tools_and_adds_google_search() -> None:
    full = ToolsSchema(
        standard_tools=[_schema(n) for n in ("smart_home", "weather", "web_answer", "wiki_summary")]
    )
    seen = with_builtin_search(full)
    assert [t.name for t in seen.standard_tools] == ["smart_home", "weather"]
    assert seen.custom_tools == {AdapterType.GEMINI: [{"google_search": {}}]}
    assert len(full.standard_tools) == 4  # the server keeps them for the router


async def _search_docs_calls(asked: str) -> tuple[list[Any], list[Any]]:
    ran: list[Any] = []
    said: list[Any] = []

    async def handler(params: Any) -> None:
        ran.append(params.arguments)
        await params.result_callback("passage")

    async def result_callback(result: Any, *, properties: Any = None) -> None:
        said.append(result)

    schema = FunctionSchema(
        name="search_docs", description="d", properties={}, required=[], handler=handler
    )
    question.current = asked
    try:
        wrapped = _recorded(schema, [])
        assert wrapped.handler is not None
        params = SimpleNamespace(arguments={"query": asked}, result_callback=result_callback)
        await wrapped.handler(params)  # type: ignore[arg-type]
    finally:
        question.current = ""
    return ran, said


@pytest.mark.parametrize(
    "asked",
    [
        "There's no cow here though. I don't see any cow in the game. Could you double check the "
        "file and fix it?",
        "I tried to play the game up to Distance 1000 and I don't see any monster.",
    ],
)
async def test_search_docs_is_not_run_for_a_turn_about_the_agents_own_work(asked: str) -> None:
    ran, said = await _search_docs_calls(asked)
    assert ran == [] and said == [agent_tools.NOT_IN_DOCUMENTS]


async def test_search_docs_runs_for_a_real_document_question() -> None:
    ran, said = await _search_docs_calls("Who is McCarthy?")
    assert len(ran) == 1 and said == ["passage"]


def test_publish_target_is_the_named_app_the_closest_name_or_the_newest(tmp_path: Path) -> None:
    for name, age in (("tech_news", 500), ("sourdough_bread", 10)):
        page = tmp_path / name / "index.html"
        page.parent.mkdir()
        page.write_text("x")
        os.utime(page, (time.time() - age, time.time() - age))
    assert agent_tools.publish_target("tech_news", tmp_path) == "tech_news"
    assert agent_tools.publish_target("technews", tmp_path) == "tech_news"
    assert agent_tools.publish_target("", tmp_path) == "sourdough_bread"
    assert agent_tools.publish_target("zzzz qqqq", tmp_path) == "sourdough_bread"
    old = tmp_path / "sourdough_bread" / "index.html"
    os.utime(old, (0, 0))
    os.utime(tmp_path / "tech_news" / "index.html", (0, 0))
    assert agent_tools.publish_target("", tmp_path) == ""


def test_same_app_paths_rewrites_spelled_variants() -> None:
    have = ["nvidia_news_update"]
    # Different spellings of the same app name
    assert same_app_paths("Update apps/nvidianewsupdate/index.html", have) == (
        "Update apps/nvidia_news_update/index.html"
    )
    assert same_app_paths("Update apps/Nvidia-News-Update/index.html", have) == (
        "Update apps/nvidia_news_update/index.html"
    )
    assert same_app_paths("Update apps/NVIDIA_NEWS_UPDATE/index.html", have) == (
        "Update apps/nvidia_news_update/index.html"
    )


def test_same_app_paths_keeps_exact_names() -> None:
    have = ["nvidia_news_update"]
    # Already correct name is unchanged
    assert same_app_paths("Update apps/nvidia_news_update/index.html", have) == (
        "Update apps/nvidia_news_update/index.html"
    )


def test_same_app_paths_ignores_unrelated_names() -> None:
    have = ["angry_birds"]
    # Unrelated names are left alone (not considered a spelling variant)
    assert same_app_paths("Create apps/angry_bird_2/index.html", have) == (
        "Create apps/angry_bird_2/index.html"
    )
    assert same_app_paths("Update apps/tech_newsletter/index.html", have) == (
        "Update apps/tech_newsletter/index.html"
    )


def test_same_app_paths_handles_multiple_paths_in_one_task() -> None:
    have = ["tech_news", "nvidia_news_update"]
    task = "Update apps/technews/page.html and apps/nvidianewsupdate/style.css"
    assert same_app_paths(task, have) == (
        "Update apps/tech_news/page.html and apps/nvidia_news_update/style.css"
    )


def test_same_app_paths_ignores_task_with_no_app_path() -> None:
    have = ["nvidia_news_update"]
    task = "Update the homepage without touching any app"
    assert same_app_paths(task, have) == task
