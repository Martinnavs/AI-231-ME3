"""Y3: smart_home's music intents reach the Pi's music tool; its sentence and event go out."""

import json
from types import SimpleNamespace
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from voice_pipeline import agent_tools, fast_path
from voice_pipeline.agent_server import AgentServer
from voice_pipeline.agent_tools import MODEL_HIDDEN, build_tools, without_tools
from voice_pipeline.router import Routed, spec


class MusicServer:
    """An MCP server with the one `music` tool, answering from a script."""

    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self.answers, self.calls = answers, []  # type: ignore[var-annotated]

    async def tools(self) -> ToolsSchema:
        async def handler(params: Any) -> None:
            self.calls.append(dict(params.arguments))
            await params.result_callback(json.dumps(self.answers[params.arguments["action"]]))

        return ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="music", description="m", properties={}, required=[], handler=handler
                )
            ]
        )


PLAY = {
    "ok": True,
    "sentence": "Playing Song 0.",
    "event": {"event": "music", "action": "play", "url": "https://x/0"},
}
INACTIVE = {"ok": False, "inactive": True, "sentence": ""}


async def run(
    server: MusicServer, **args: Any
) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    record: list[dict[str, Any]] = []
    tools = await build_tools(record, optional=[server])
    home = next(t for t in tools.standard_tools if t.name == "smart_home")
    got: list[Any] = []
    events: list[dict[str, Any]] = []

    async def result_callback(result: Any, *, properties: Any = None) -> None:
        got.append((result, properties))

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    agent_tools.set_event_sink(sink if args.pop("client", True) else None)
    try:
        assert home.handler is not None
        await home.handler(
            SimpleNamespace(arguments=args, tool_call_id="1", result_callback=result_callback)
        )
    finally:
        agent_tools.set_event_sink(None)
    return got, events, record


async def test_play_music_with_a_query_goes_to_the_tool_and_the_event_to_the_client() -> None:
    server = MusicServer({"play": PLAY})
    got, events, record = await run(server, intent="PLAY_MUSIC", slot="Taylor Swift")
    assert server.calls == [{"action": "play", "query": "Taylor Swift"}]
    assert got[0][0] == "Playing Song 0." and got[0][1].run_llm is False
    assert events == [PLAY["event"]] and record == [
        {"intent": "PLAY_MUSIC", "slot": "Taylor Swift"}
    ]


async def test_each_music_intent_maps_to_its_action() -> None:
    for intent, action in agent_tools.MUSIC_INTENTS.items():
        server = MusicServer({action: {"ok": True, "sentence": "ok"}})
        await run(server, intent=intent)
        assert server.calls == [{"action": action, "query": ""}], intent


async def test_with_nothing_playing_the_old_behaviour_stays() -> None:
    for intent in ("PAUSE", "STOP", "NEXT", "VOLUME_UP", "VOLUME_DOWN"):
        server = MusicServer({agent_tools.MUSIC_INTENTS[intent]: INACTIVE})
        got, events, _ = await run(server, intent=intent)
        assert got[0][0] == {"ok": True, "message": "recorded"} and events == [], intent


async def test_other_intents_never_reach_the_music_tool() -> None:
    server = MusicServer({})
    got, _, _ = await run(server, intent="LIGHT_ON")
    assert server.calls == [] and got[0][0]["ok"]


async def test_a_failure_is_spoken() -> None:
    fail = {"ok": False, "sentence": "I couldn't find any music for that."}
    got, events, _ = await run(MusicServer({"play": fail}), intent="PLAY_MUSIC", slot="qzxv")
    assert got[0][0] == "I couldn't find any music for that." and events == []


async def test_without_a_client_to_play_it_says_so() -> None:
    got, events, _ = await run(MusicServer({"play": PLAY}), intent="PLAY_MUSIC", client=False)
    assert got[0][0] == "I can't play audio right now." and events == []


async def test_the_model_does_not_see_the_music_tool() -> None:
    tools = await build_tools([], optional=[MusicServer({})])
    assert "music" in [t.name for t in tools.standard_tools]
    assert "music" not in [t.name for t in without_tools(tools, MODEL_HIDDEN).standard_tools]


def test_the_fast_path_says_the_tools_sentence_instead_of_the_fixed_one() -> None:
    found = fast_path.match("play some music")
    assert found is not None and found.say == "Playing music."
    assert fast_path.spoken_result(found, "Playing Song 0.") == "Playing Song 0."
    assert fast_path.spoken_result(found, {"ok": True, "message": "recorded"}) == "Playing music."


async def test_the_router_path_speaks_a_string_result(tmp_path: Any) -> None:
    class Home:
        name = "smart_home"

        async def handler(self, params: Any) -> None:
            await params.result_callback("Playing Song 0.")

    tools = SimpleNamespace(standard_tools=[Home()])
    server = AgentServer(None, [], tmp_path / "log.jsonl", tools)
    said: list[str] = []

    async def on_text(t: str) -> None:
        said.append(t)

    routed = Routed(spec.parse_output("PLAY_MUSIC"), "PLAY_MUSIC", 0.1, None, "play some music")
    done = await server.run_routed(routed, 0.0, on_text)
    assert done is not None and said == ["Playing Song 0."]


def test_the_spotify_source_loads_the_music_tool_without_a_resolver(monkeypatch: Any) -> None:
    """HARNESS=laptop has no resolver; Spotify needs none (Web API search, desktop app plays)."""
    monkeypatch.setattr(agent_tools, "MUSIC_RESOLVER_URL", None)
    monkeypatch.setenv("MUSIC_SOURCE", "spotify")
    command = agent_tools.music_command()
    assert command is not None and command[-2:] == ["--source", "spotify"]
    assert "--resolver-url" not in command
    monkeypatch.setenv("MUSIC_SOURCE", "youtube")
    assert agent_tools.music_command() is None  # YouTube cannot search without the resolver
    monkeypatch.setattr(agent_tools, "MUSIC_RESOLVER_URL", "http://10.0.0.1:8796")
    with_resolver = agent_tools.music_command()
    assert with_resolver is not None and "--resolver-url" in with_resolver
    monkeypatch.setenv("MUSIC_SOURCE", "spotify")
    both = agent_tools.music_command()
    assert both is not None and "--resolver-url" in both and both[-2:] == ["--source", "spotify"]


def test_the_default_source_comes_from_the_music_config(monkeypatch: Any) -> None:
    monkeypatch.delenv("MUSIC_SOURCE", raising=False)
    assert agent_tools.music_source() in ("spotify", "youtube")


async def test_without_a_music_tool_a_play_request_says_music_is_not_connected() -> None:
    tools = await build_tools([])  # no dashboard (live None), no music server
    home = tools.standard_tools[0]
    said: list[str] = []

    async def result_callback(result: Any, *, properties: Any = None) -> None:
        said.append(str(result))

    def call(intent: str) -> SimpleNamespace:
        return SimpleNamespace(
            arguments={"intent": intent, "slot": "Taylor Swift"},
            result_callback=result_callback,
            tool_call_id="x",
            function_name="smart_home",
        )

    assert home.handler is not None
    await home.handler(call("PLAY_MUSIC"))  # type: ignore[arg-type]
    await home.handler(call("NEXT"))  # type: ignore[arg-type]
    assert said == [agent_tools.NO_MUSIC, agent_tools.NO_MUSIC]
    assert "Playing" not in said[0]
