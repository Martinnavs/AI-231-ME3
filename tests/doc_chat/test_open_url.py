"""Y2: the client opens a page of the Pi's apps server, and nothing else."""

import argparse
import io
from typing import Any

import pytest

pytest.importorskip("langchain_core")

import doc_chat.__main__ as main_mod  # noqa: E402
from doc_chat.__main__ import Session  # noqa: E402
from doc_chat.dialogue import ChatHistory  # noqa: E402


def make(agent_url: str = "ws://169.254.7.7:8781") -> tuple[Session, list[list[str]]]:
    calls: list[list[str]] = []
    s = Session(
        args=argparse.Namespace(agent_url=agent_url),
        client=object(),  # type: ignore[arg-type]
        log_file=io.StringIO(),
        out_dir=None,
        history=ChatHistory(4),
    )

    class Popen:
        def __init__(self, argv: list[str], **kw: Any) -> None:
            calls.append(argv)

    main_mod.subprocess.Popen = Popen  # type: ignore[assignment,misc]
    return s, calls


@pytest.fixture(autouse=True)
def restore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_mod.subprocess, "Popen", main_mod.subprocess.Popen)
    monkeypatch.delenv("OPEN_APP_COMMAND", raising=False)


def test_an_app_page_on_the_agents_host_is_opened() -> None:
    s, calls = make()
    s.on_agent_event({"event": "open_url", "url": "http://169.254.7.7:8795/angry_birds/"})
    assert calls == [["xdg-open", "http://169.254.7.7:8795/angry_birds/"]]


def test_the_opener_can_be_replaced_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_APP_COMMAND", "/tmp/stub")
    s, calls = make()
    assert s.open_url("http://169.254.7.7:8795/pong/")
    assert calls == [["/tmp/stub", "http://169.254.7.7:8795/pong/"]]


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example:8795/pong/",
        "http://169.254.7.8:8795/pong/",
        "http://169.254.7.7:9999/pong/",
        "https://169.254.7.7:8795/pong/",
        "http://169.254.7.7:8795/pong",
        "http://169.254.7.7:8795/pong/x",
        "http://169.254.7.7:8795/../etc/",
        "http://169.254.7.7:8795/Po ng/",
        "file:///etc/passwd",
        "http://169.254.7.7:8795/pong/?x=1",
        "http://169.254.7.7@evil.example:8795/pong/",
        "",
    ],
)
def test_any_other_url_is_refused(url: str) -> None:
    s, calls = make()
    s.on_agent_event({"event": "open_url", "url": url})
    assert calls == []


def test_it_needs_no_event_loop_and_other_events_still_need_one() -> None:
    s, calls = make()
    assert s._loop is None
    s.on_agent_event({"event": "tool", "name": "weather"})  # ignored without a loop
    assert calls == []
