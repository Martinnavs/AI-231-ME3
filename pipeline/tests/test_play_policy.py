"""V1: "play" is overloaded: the router keeps bare music, targets go to Gemini."""

from types import SimpleNamespace
from typing import Any

import pytest

from voice_pipeline import call_policy
from voice_pipeline.agent_tools import open_app
from voice_pipeline.router import spec
from voice_pipeline.tools.smart_home import make_handler, make_schema, to_command

AVAILABLE = {"smart_home", "open_app", "weather", "web_answer", "delegate", "get_volume"}


@pytest.fixture(autouse=True)
def apps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(call_policy, "known_apps", lambda: ["angry_birds", "flappy_bird"])


def reason(lines: str, user: str) -> str | None:
    return call_policy.decide(spec.parse_output(lines), user, AVAILABLE).reason


@pytest.mark.parametrize(
    "user",
    [
        "play music",
        "Play some music.",
        "Play something.",
        "Play.",
        "Can you play some music?",
        "Please play the music.",
        "Start the music.",
        "play my playlist",
        "put on some music",
        "Some music please.",
        "Play the next song.",
        "Turn off the lights and play some music.",
    ],
)
def test_bare_music_stays_with_the_router(user: str) -> None:
    assert reason("PLAY_MUSIC", user) is None


@pytest.mark.parametrize(
    "user",
    [
        "Can I play the Angry Birds clone?",
        "Play angry birds.",
        "Play the game.",
        "Play a game.",
        "Can I play it?",
        "Let me play the game you made.",
        "Play flappy bird.",
        "Play Taylor Swift.",
        "Play some jazz.",
        "Play some OPM.",
        "Play something relaxing.",
        "Play Taylor Swift and turn off the lights.",
        "Put on some jazz.",
        "I want to listen to Taylor Swift.",
    ],
)
def test_a_named_target_goes_to_gemini(user: str) -> None:
    assert reason("PLAY_MUSIC", user) == "play: names a target"


def test_resume_is_play_music_not_stop() -> None:
    assert reason("STOP", "Resume the music.") == "resume is not STOP"
    assert reason("PLAY_MUSIC", "Resume the music.") is None
    assert reason("STOP", "Stop the music.") is None


def test_other_home_intents_on_a_play_or_open_turn_go_to_gemini_but_timers_do_not() -> None:
    assert reason("LIGHT_ON", "Open the angry birds lights") == "LIGHT_ON for a play/open turn"
    assert reason("TIMER|5 minutes", "Start a timer for 5 minutes") is None
    assert reason("LIGHT_OFF\nPLAY_MUSIC", "Turn off the lights and play some music.") is None


def test_a_sorry_on_a_turn_naming_an_existing_app_goes_to_gemini() -> None:
    sorry = "none|Sorry, I didn't catch that."
    assert reason(sorry, "Open angry birds.") == "names an app"
    assert reason(sorry, "Open the flappy bird.") == "names an app"
    assert reason(sorry, "Mumble mumble.") is None


def test_the_apps_listing_is_cached_for_a_few_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    from voice_pipeline import agent_tools

    calls: list[int] = []
    monkeypatch.undo()  # the real known_apps, with a counting lister
    monkeypatch.setattr(agent_tools, "app_names", lambda *a: calls.append(1) or ["pong"])
    monkeypatch.setattr(call_policy, "_apps_cache", (0.0, []))
    assert call_policy.known_apps() == ["pong"] and call_policy.known_apps() == ["pong"]
    assert len(calls) == 1
    monkeypatch.setattr(call_policy.time, "monotonic", lambda: 1e9)
    call_policy.known_apps()
    assert len(calls) == 2


def test_the_music_slot_is_in_the_schema_and_the_command() -> None:
    schema = make_schema([])
    assert "PLAY_MUSIC" in schema.description and "artist" in schema.description
    assert "PLAY_MUSIC" in schema.properties["slot"]["description"]
    assert to_command("PLAY_MUSIC", "Taylor Swift") == {
        "intent": "PLAY_MUSIC",
        "slots": {"QUERY": "Taylor Swift"},
    }
    assert to_command("PLAY_MUSIC", None) == {"intent": "PLAY_MUSIC", "slots": {}}
    assert to_command("PAUSE", "x") == {"intent": "PAUSE", "slots": {}}


async def test_the_player_records_the_query_and_confirms_it() -> None:
    record: list[dict[str, Any]] = []
    handler = make_handler(base_url=None, record=record, contacts=[])
    got: list[Any] = []

    async def done(result: Any, *, properties: Any = None) -> None:
        got.append(result)

    for slot in ("Taylor Swift.", None):
        params = SimpleNamespace(
            arguments={"intent": "PLAY_MUSIC", "slot": slot}, result_callback=done
        )
        await handler(params)  # type: ignore[arg-type]
    assert record == [
        {"intent": "PLAY_MUSIC", "slot": "Taylor Swift."},
        {"intent": "PLAY_MUSIC", "slot": None},
    ]
    assert got[0] == {"ok": True, "message": "Playing Taylor Swift."}
    assert got[1] == {"ok": True, "message": "recorded"}  # bare music: as before


def test_open_app_on_a_generic_request_lists_the_apps_and_asks(tmp_path: Any) -> None:
    root = tmp_path / "apps"
    for name in ("angry_birds", "flappy_bird"):
        (root / name).mkdir(parents=True)
        (root / name / "index.html").write_text("x")
    for generic in ("the game", "the clone", "it", "game", "a game"):
        said, url = open_app(generic, "open the game", root)
        assert url is None and said == "I have angry birds, flappy bird. Which one do you want?"
        assert "make" not in said
    assert "any apps yet" in open_app("the game", "open the game", tmp_path / "none")[0]
    assert "Want me to make it?" in open_app("tetris", "open tetris", root)[0]  # a real name


def test_the_prompt_tells_gemini_about_the_music_slot_and_games() -> None:
    from pathlib import Path

    text = (Path(__file__).parents[1] / "config/eval/system_agent.txt").read_text()
    assert "Taylor's wift" in text and "open_app" in text and "empty name" in text
