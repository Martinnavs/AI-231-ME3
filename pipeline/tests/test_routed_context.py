"""Routed lookups that need the conversation, temperature questions, and app names (2026-11-12)."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_pipeline import call_policy, question
from voice_pipeline.agent_server import AgentServer
from voice_pipeline.agent_tools import closest_app, open_app, spoken_apps
from voice_pipeline.router import Routed


def call(name: str, *args: str) -> Any:
    return SimpleNamespace(name=name, args=list(args))


def test_a_lookup_that_still_holds_a_reference_needs_the_conversation() -> None:
    needs = call_policy.lookup_needs_context
    assert needs([call("web_answer", "Who is the current chairperson at the program?")], "x")
    assert needs([call("web_answer", "When was he born?")], "x")
    assert needs([call("search_docs", "What does that company do?")], "x")
    assert not needs([call("web_answer", "Who is the mayor of Cebu City?")], "x")
    assert not needs([call("weather", "the program")], "x")  # only lookups
    assert not needs([call("PLAY_MUSIC", "its song")], "x")


def test_a_temperature_question_is_a_forecast_for_the_users_place() -> None:
    weather = call_policy.weather_args
    assert weather("What's the current temp about temperature?") == {"place": "", "day": "today"}
    assert weather("How hot is it outside right now?") == {"place": "", "day": "today"}
    assert weather("Will it rain tomorrow?") == {"place": "", "day": "tomorrow"}
    assert weather("What is the temperature in Tokyo right now?") == {
        "place": "Tokyo",
        "day": "today",
    }
    assert weather("What is the temperature of the sun right now") is None
    assert weather("What's the Pi temperature now?") is None  # the device, not the weather
    assert weather("Who won the match today") is None


def test_plan_swaps_a_web_temperature_lookup_for_the_weather_tool() -> None:
    steps = call_policy.plan(
        [call("web_answer", "current temperature")],
        "What's the current temp about temperature?",
        {"weather", "web_answer"},
    )
    assert [(s.tool, s.args) for s in steps] == [("weather", {"place": "", "day": "today"})]
    kept = call_policy.plan(
        [call("web_answer", "who is he")], "who is he", {"weather", "web_answer"}
    )
    assert kept[0].tool == "web_answer"  # no weather words: the lookup stays
    no_tool = call_policy.plan(
        [call("web_answer", "temp now")], "What's the temp now?", {"web_answer"}
    )
    assert no_tool[0].tool == "web_answer"  # nothing to swap to


class Router:
    async def route(self, history: Any, user: str, names: Any) -> Routed:
        question = "Who is the current chairperson at the program?"
        return Routed(
            calls=[call("web_answer", question)], lines=f"web_answer|{question}", user=user
        )


class Agent:
    def __init__(self) -> None:
        self.asked = 0

    async def ask(self, messages: Any, *, on_text: Any, **_: Any) -> dict[str, Any]:
        self.asked += 1
        await on_text("The coordinator is the one for the AI program.")
        return {"status": "ok", "text": "x", "first_call_s": 0, "first_text_s": 0, "end_s": 0}

    async def cancel(self) -> None:
        return None


async def test_a_routed_lookup_with_a_reference_goes_to_the_model_with_the_history(
    tmp_path: Path,
) -> None:
    agent = Agent()
    server = AgentServer(agent, [], tmp_path / "log.jsonl", router=Router())  # type: ignore[arg-type]
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(ws_server.sockets)).getsockname()[1]}"
    history = [
        {"role": "user", "content": "what is the leading AI programme at UP Diliman"},
        {"role": "assistant", "content": "It is the Master of Engineering in AI."},
        {"role": "user", "content": "Who is the current chairperson at the program?"},
    ]
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": history}))
        while "done" not in json.loads(await ws.recv()):
            pass
    ws_server.close()
    question.current = ""  # the server sets it per turn; do not leak it into other tests
    assert agent.asked == 1
    row = json.loads((tmp_path / "log.jsonl").read_text())
    assert row["router_reason"] == "the question refers back to the conversation"


def test_one_distinctive_word_finds_the_app_and_the_list_is_short(tmp_path: Path) -> None:
    have = ["about_me", "angry_birds", "annie_billard", "billiards", "calculator", "city_temps"]
    assert closest_app("annie", have) == "annie_billard"
    assert closest_app("the Annie website", have) == "annie_billard"
    assert closest_app("billiards", have) == "billiards"
    assert closest_app("bird", ["angry_birds", "flappy_bird"]) is None  # two apps hold it
    assert (
        spoken_apps(have)
        == "about me, angry birds, annie billard, billiards, calculator, and 1 more"
    )
    assert spoken_apps(["a", "b"]) == "a, b"
    for name in have:
        (tmp_path / name).mkdir()
        (tmp_path / name / "index.html").write_text("x")
    said, url = open_app("", "open one", root=tmp_path)
    assert url is None and said.count(",") <= 5 and "and 1 more" in said
