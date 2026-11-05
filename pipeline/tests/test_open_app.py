"""T2 and T4: open_app opens only an existing app, only on request, only at 127.0.0.1:8795."""

from pathlib import Path
from typing import Any

import pytest

from voice_pipeline import agent_tools, question
from voice_pipeline.agent_tools import SPOKEN_RESULT_TOOLS, app_names, build_tools, open_app


@pytest.fixture
def apps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[list[str]]]:
    root = tmp_path / "apps"
    (root / "flappy_bird").mkdir(parents=True)
    (root / "flappy_bird" / "index.html").write_text("<canvas></canvas>")
    (root / "empty").mkdir()  # no index.html: not an app
    (root / "Bad Name").mkdir()
    (root / "Bad Name" / "index.html").write_text("x")
    calls: list[list[str]] = []

    class Popen:
        def __init__(self, argv: list[str], **kw: Any) -> None:
            calls.append(argv)

    monkeypatch.setattr(agent_tools.subprocess, "Popen", Popen)
    monkeypatch.setattr(agent_tools, "APPS_ROOT", root)
    return root, calls


def test_only_folders_with_an_index_and_a_safe_name_are_apps(apps) -> None:  # type: ignore[no-untyped-def]
    assert app_names(apps[0]) == ["flappy_bird"]


def test_it_opens_the_url_on_loopback_when_asked(apps) -> None:  # type: ignore[no-untyped-def]
    said, url = open_app("flappy_bird", "Can I play the flappy bird clone?", apps[0])
    assert said == "Opening flappy bird in your browser."
    assert url == "http://127.0.0.1:8795/flappy_bird/"
    assert apps[1] == [[agent_tools.OPEN_COMMAND, "http://127.0.0.1:8795/flappy_bird/"]]


def test_a_spoken_name_with_a_space_and_a_yes_after_an_offer_work(apps) -> None:  # type: ignore[no-untyped-def]
    assert open_app("Flappy Bird", "yes", apps[0])[1] == "http://127.0.0.1:8795/flappy_bird/"
    assert open_app("flappy_bird", "Open it.", apps[0])[1] is not None


def test_nothing_opens_unless_the_user_asked(apps) -> None:  # type: ignore[no-untyped-def]
    said, url = open_app("flappy_bird", "What is the weather in Manila?", apps[0])
    assert url is None and apps[1] == [] and "ask" in said


@pytest.mark.parametrize("name", ["../etc", "a/b", "flappy_bird/../x", "http://evil.test", "x;rm"])
def test_a_name_that_is_not_a_plain_app_name_is_refused(apps, name: str) -> None:  # type: ignore[no-untyped-def]
    said, url = open_app(name, "open it", apps[0])
    assert url is None and apps[1] == []


def test_a_missing_app_is_said_and_the_offer_made(apps) -> None:  # type: ignore[no-untyped-def]
    said, url = open_app("pong", "open pong", apps[0])
    assert url is None and apps[1] == [] and "don't have an app called pong" in said
    assert "Want me to make it?" in said


def test_an_empty_name_lists_the_apps(apps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    assert open_app("", "which apps", apps[0])[0] == "I have flappy bird. Which one do you want?"
    assert "don't have any apps" in open_app("", "x", tmp_path / "none")[0]


def test_the_sentence_is_spoken_as_it_is_with_no_second_request() -> None:
    assert "open_app" in SPOKEN_RESULT_TOOLS
    assert not agent_tools.FAILURE.search("I don't have an app called pong. Want me to make it?")


async def test_the_tool_is_offered_and_answers_with_the_sentence(apps) -> None:  # type: ignore[no-untyped-def]
    tools = await build_tools([])
    schema = next(t for t in tools.standard_tools if t.name == "open_app")
    assert set(schema.properties) == {"name", "say"} and schema.required == ["name"]
    got: list[Any] = []

    async def result_callback(result: Any, *, properties: Any = None) -> None:
        got.append((result, properties))

    class Params:
        arguments = {"name": "flappy_bird", "say": "Opening it."}
        tool_call_id = "1"

    params = Params()
    params.result_callback = result_callback  # type: ignore[attr-defined]
    question.current = "open the flappy bird game"
    try:
        assert schema.handler is not None
        await schema.handler(params)  # type: ignore[arg-type]
    finally:
        question.current = ""
    assert got[0][0] == "Opening flappy bird in your browser." and got[0][1].run_llm is False
    assert apps[1] == [[agent_tools.OPEN_COMMAND, "http://127.0.0.1:8795/flappy_bird/"]]


def test_a_name_is_matched_to_the_closest_existing_app(tmp_path: Path) -> None:
    from voice_pipeline.agent_tools import app_key, closest_app

    have = ["angry_birds", "flappy-bird", "flappy_bird", "pong"]
    assert closest_app("angry birds clone", have) == "angry_birds"
    assert closest_app("Angry Bird game", have) == "angry_birds"
    assert closest_app("flappy bird", have) == "flappy-bird"  # same key: the first listed
    assert closest_app("flappy_bird", have) == "flappy_bird"  # exact name wins
    assert closest_app("angry-birds", ["angry_birds"]) == "angry_birds"
    assert closest_app("pongo", have) == "pong"  # a near spelling
    assert closest_app("tetris", have) is None and closest_app("the game", have) is None
    assert app_key("The Angry Birds Clone App") == "angry bird"


def test_the_fuzzy_name_opens_the_right_url_and_an_unknown_one_lists_what_exists(apps) -> None:  # type: ignore[no-untyped-def]
    root, calls = apps
    (root / "angry_birds").mkdir()
    (root / "angry_birds" / "index.html").write_text("x")
    said, url = open_app("angry birds clone", "open the angry birds clone", root)
    assert url == "http://127.0.0.1:8795/angry_birds/" and "angry birds" in said
    assert calls == [[agent_tools.OPEN_COMMAND, url]]
    said, url = open_app("tetris", "open tetris", root)
    assert (
        url is None and "I have angry birds, flappy bird." in said and "Want me to make it" in said
    )
