"""Y2: with an apps host (HARNESS=pi) open_app sends the client a URL, not a browser start."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voice_pipeline import agent_tools, question
from voice_pipeline.agent_tools import build_tools, open_app


@pytest.fixture
def apps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[list[str]]]:
    root = tmp_path / "apps"
    (root / "flappy_bird").mkdir(parents=True)
    (root / "flappy_bird" / "index.html").write_text("x")
    popen: list[list[str]] = []

    class Popen:
        def __init__(self, argv: list[str], **kw: Any) -> None:
            popen.append(argv)

    monkeypatch.setattr(agent_tools.subprocess, "Popen", Popen)
    monkeypatch.setattr(agent_tools, "APPS_ROOT", root)
    monkeypatch.setattr(agent_tools, "APPS_HOST", None)
    monkeypatch.setattr(agent_tools, "EVENT_SINK", None)
    return root, popen


def test_with_a_host_the_url_points_at_the_pi_and_nothing_is_started_here(apps) -> None:  # type: ignore[no-untyped-def]
    root, popen = apps
    said, url = open_app("flappy bird", "open it", root, host="169.254.7.7")
    assert url == "http://169.254.7.7:8795/flappy_bird/" and popen == []
    assert said == "Opening flappy bird in your browser."


def test_the_confirmation_gate_is_unchanged_with_a_host(apps) -> None:  # type: ignore[no-untyped-def]
    root, _ = apps
    said, url = open_app("flappy bird", "what is the weather", root, host="169.254.7.7")
    assert url is None and "ask" in said


async def _run(name: str, asked: str) -> tuple[list[Any], list[dict[str, Any]]]:
    tools = await build_tools([])
    schema = next(t for t in tools.standard_tools if t.name == "open_app")
    got: list[Any] = []
    events: list[dict[str, Any]] = []

    async def result_callback(result: Any, *, properties: Any = None) -> None:
        got.append(result)

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    agent_tools.set_event_sink(sink)
    question.current = asked
    try:
        params = SimpleNamespace(
            arguments={"name": name}, tool_call_id="1", result_callback=result_callback
        )
        assert schema.handler is not None
        await schema.handler(params)  # type: ignore[arg-type]
    finally:
        question.current = ""
        agent_tools.set_event_sink(None)
    return got, events


async def test_the_tool_sends_an_open_url_event_to_the_client(apps, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(agent_tools, "APPS_HOST", "169.254.7.7")
    got, events = await _run("flappy_bird", "Yes, open it.")
    assert events == [{"event": "open_url", "url": "http://169.254.7.7:8795/flappy_bird/"}]
    assert got == ["Opening flappy bird in your browser."] and apps[1] == []


async def test_without_a_client_attached_it_says_so(apps, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(agent_tools, "APPS_HOST", "169.254.7.7")
    tools = await build_tools([])
    schema = next(t for t in tools.standard_tools if t.name == "open_app")
    got: list[Any] = []

    async def result_callback(result: Any, *, properties: Any = None) -> None:
        got.append(result)

    question.current = "open it"
    try:
        params = SimpleNamespace(
            arguments={"name": "flappy_bird"}, tool_call_id="1", result_callback=result_callback
        )
        await schema.handler(params)  # type: ignore[arg-type,misc]
    finally:
        question.current = ""
    assert got == ["I can't open a page from here right now."]


async def test_without_an_apps_host_the_laptop_harness_opens_the_browser_itself(apps) -> None:  # type: ignore[no-untyped-def]
    got, events = await _run("flappy_bird", "open it")
    assert events == [] and apps[1] == [
        [agent_tools.OPEN_COMMAND, "http://127.0.0.1:8795/flappy_bird/"]
    ]
