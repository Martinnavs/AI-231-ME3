import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from websockets.sync.server import ServerConnection, serve

from doc_chat.agent_client import AgentChatModel, to_wire


class FakeAgent:
    """A one-reply agent server that records what it received."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.received: list[dict[str, Any]] = []

    def handle(self, ws: ServerConnection) -> None:
        self.received.append(json.loads(ws.recv()))
        for event in self.events:
            ws.send(json.dumps(event))


@pytest.fixture
def agent(request: pytest.FixtureRequest) -> Iterator[tuple[FakeAgent, str]]:
    fake = FakeAgent(request.param)
    with serve(fake.handle, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield fake, f"ws://127.0.0.1:{port}"
        server.shutdown()


def test_to_wire_maps_roles() -> None:
    messages = [SystemMessage("s"), HumanMessage("q"), AIMessage("a")]
    assert to_wire(messages) == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]


@pytest.mark.parametrize(
    "agent", [[{"text": "Lights "}, {"text": "on."}, {"done": True}]], indirect=True
)
def test_stream_yields_text_until_done(agent: tuple[FakeAgent, str]) -> None:
    fake, url = agent
    stream = AgentChatModel(url=url).stream([HumanMessage("Lights on")])
    chunks = [c.content for c in stream if c.content]
    assert chunks == ["Lights ", "on."]
    assert fake.received == [{"messages": [{"role": "user", "content": "Lights on"}]}]


@pytest.mark.parametrize("agent", [[{"error": "boom"}]], indirect=True)
def test_server_error_raises(agent: tuple[FakeAgent, str]) -> None:
    _, url = agent
    with pytest.raises(RuntimeError, match="boom"):
        list(AgentChatModel(url=url).stream([HumanMessage("x")]))


@pytest.mark.parametrize(
    "agent",
    [[{"text": "Checking. ", "flush": True}, {"text": "It is 20."}, {"done": True}]],
    indirect=True,
)
def test_flush_marks_the_sentence_and_the_first_text_is_an_event(
    agent: tuple[FakeAgent, str],
) -> None:
    from doc_chat.chain import FLUSH

    _, url = agent
    events: list[dict[str, Any]] = []
    model = AgentChatModel(url=url, on_event=events.append)
    chunks = [c.content for c in model.stream([HumanMessage("q")]) if c.content]
    assert chunks == ["Checking. " + FLUSH, "It is 20."]
    assert events == [{"event": "text"}]
    assert model.invoke([HumanMessage("q")]).content == "Checking. It is 20."


def _serve_recording_auth(seen: list[str | None]) -> Any:
    def auth(connection: Any, request: Any) -> None:
        seen.append(request.headers.get("Authorization"))

    def handle(ws: ServerConnection) -> None:
        ws.recv()
        ws.send(json.dumps({"text": "ok"}))
        ws.send(json.dumps({"done": True}))

    return serve(handle, "127.0.0.1", 0, process_request=auth)


@pytest.mark.parametrize(
    ("field", "env", "expected"),
    [
        ("given", "from-env", "Bearer given"),
        (None, "from-env", "Bearer from-env"),
        (None, "", None),
    ],
)
def test_client_sends_the_session_token(
    monkeypatch: pytest.MonkeyPatch, field: str | None, env: str, expected: str | None
) -> None:
    monkeypatch.setenv("LAN_SPEECH_TOKEN", env)
    seen: list[str | None] = []
    with _serve_recording_auth(seen) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        list(AgentChatModel(url=f"ws://127.0.0.1:{port}", token=field).stream([HumanMessage("x")]))
        server.shutdown()
    assert seen == [expected]
