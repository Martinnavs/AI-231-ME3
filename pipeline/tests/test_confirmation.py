"""U2: a short yes to the assistant's own offer goes to Gemini, not to the 350M router."""

import json
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_pipeline.agent_server import AgentServer
from voice_pipeline.call_policy import answers_an_offer

OFFER = "The game is ready. Would you like me to open it?"


def turn(reply: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "is it done"},
        {"role": "assistant", "content": reply},
        {"role": "user", "content": user},
    ]


@pytest.mark.parametrize(
    "user", ["Yep, please do.", "In the browser yep.", "Yes", "yeah sure", "Please do it.", "no"]
)
def test_a_short_confirmation_after_an_offer_goes_to_gemini(user: str) -> None:
    assert answers_an_offer(turn(OFFER, user))


@pytest.mark.parametrize(
    "reply",
    ["The lights are on.", "Okay.", "It is 54 degrees."],
)
def test_without_an_offer_before_it_the_router_keeps_the_turn(reply: str) -> None:
    assert not answers_an_offer(turn(reply, "Yep, please do."))


@pytest.mark.parametrize(
    "user",
    [
        "What is the capital of France?",
        "Yes, tell me about the Roman empire and its history",
        "Thanks",
        "",
    ],
)
def test_only_short_yes_no_turns_are_taken(user: str) -> None:
    assert not answers_an_offer(turn(OFFER, user))


def test_offers_are_a_question_mark_or_an_offer_phrase() -> None:
    for reply in (
        "Shall I?",
        "Say open it to play it.",
        "Do you want that now",
        "Want me to do it.",
    ):
        assert answers_an_offer(turn(reply, "yes")), reply


def test_the_routers_own_offer_stays_with_the_router() -> None:
    reply = "That sounds chilly. Want me to warm the room?"
    assert answers_an_offer(turn(reply, "Yep, sure."))
    assert not answers_an_offer(turn(reply, "Yep, sure."), {reply: "none|..."})


def test_a_first_turn_or_an_assistant_last_is_not_an_answer() -> None:
    assert not answers_an_offer([{"role": "user", "content": "yes"}])
    assert not answers_an_offer(turn(OFFER, "yes")[:2])


class Agent:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, messages: list[dict[str, Any]], *, on_text: Any, **_: Any) -> dict:
        self.asked.append(messages[-1]["content"])
        await on_text("Opening it.")
        return {
            "status": "ok",
            "text": "Opening it.",
            "first_call_s": 0.1,
            "first_text_s": 0.1,
            "end_s": 0.1,
        }

    async def cancel(self) -> None:
        return None


class Router:
    calls = 0

    async def route(self, *a: Any, **k: Any) -> Any:
        Router.calls += 1
        raise AssertionError("the router must not see this turn")


async def test_the_server_sends_the_confirmation_to_gemini_without_asking_the_router(
    tmp_path: Path,
) -> None:
    agent = Agent()
    server = AgentServer(agent, [], tmp_path / "log.jsonl", router=Router())  # type: ignore[arg-type]
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(ws_server.sockets)).getsockname()[1]}"
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": turn(OFFER, "Yep, please do.")}))
        while "done" not in json.loads(await ws.recv()):
            pass
    assert agent.asked == ["Yep, please do."] and Router.calls == 0
    ws_server.close()
    await ws_server.wait_closed()


def test_the_notice_that_offers_to_open_an_app_is_an_offer() -> None:
    notice = (
        "Your earlier request is done: The angry birds web version is ready; "
        "say open it to play it."
    )
    history = [
        {"role": "user", "content": "(a background job finished)"},
        {"role": "assistant", "content": notice},
        {"role": "user", "content": "Yep, please do."},
    ]
    assert answers_an_offer(history)
