"""What the listener hears: restated status sentences, premature claims, backchannels, and a
restated request that replaces the running job (2026-11-12)."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_pipeline import agent_tools
from voice_pipeline.agent_server import AgentServer
from voice_pipeline.spoken_filter import SentenceFilter, is_apology, is_backchannel, repeated


def stream(f: SentenceFilter, pieces: list[str]) -> str:
    return "".join(f.feed(p) for p in pieces) + f.finish()


def test_a_status_sentence_that_restates_the_one_before_is_dropped() -> None:
    f = SentenceFilter()
    said = stream(
        f,
        [
            "I am updating the restaurant website to include the genre. ",
            "OK, I am updating the website with those holidays. ",
            "I will let you know.",
        ],
    )
    assert "holidays" not in said and "restaurant website" in said and "let you know" in said
    assert f.dropped == ["OK, I am updating the website with those holidays."]


def test_the_say_sentence_counts_as_the_one_before() -> None:
    f = SentenceFilter()
    f.note("I am updating the restaurant website for you. ")
    said = stream(
        f, ["OK, I am updating the website now with the genre. ", "It will take a moment."]
    )
    assert "updating the website now" not in said and "moment" in said


def test_a_completion_claim_is_dropped_only_when_the_turn_started_a_job() -> None:
    text = ["I am adding the title. ", "I've added that to your page."]
    started = SentenceFilter(lambda: True)
    assert "added that" not in stream(started, text)
    plain = SentenceFilter(lambda: False)
    assert "added that" in stream(plain, text)  # no job in this turn: a claim about a past one


def test_ordinary_sentences_stream_through_at_once() -> None:
    f = SentenceFilter()
    assert f.feed("Lights ") == "Lights " and f.feed("on.") == "on."
    assert f.feed(" Then ") == " Then "
    assert (
        f.feed("the weather is fine. It is") == "the weather is fine. "
    )  # "It is" may be a status
    assert f.feed(" 25 degrees.") == "" and f.finish() == "It is 25 degrees. "


def test_a_boundary_split_over_two_pieces_still_ends_the_sentence() -> None:
    f = SentenceFilter(lambda: True)
    out = [f.feed(p) for p in ["Today it rains.", " ", "I've updated it. ", "Bye."]]
    assert "".join(out).startswith("Today it rains. ") and "updated" not in "".join(out)


def test_repeated_uses_the_same_rule_as_the_filter() -> None:
    said = (
        "I am building the restaurant website for you. I am building the website now, ready soon."
    )
    assert repeated(said)
    assert not repeated("Looking up Marie Curie for you. Marie Curie was a physicist.")


def test_backchannels_and_apologies_are_not_requests() -> None:
    for said in ("Mm-hmm.", "uh huh", "Hmm.", "I'm sorry.", "Sorry", "Oops!"):
        assert is_backchannel(said) or is_apology(said), said
    for said in ("Yes", "okay", "sorry I mean play it", "Hmm what is that", "No"):
        assert not (is_backchannel(said) or is_apology(said)), said


class Agent:
    def __init__(self, record: list[dict[str, Any]], pieces: list[str], tools: list[str]) -> None:
        self.record, self.pieces, self.tools = record, pieces, tools
        self.asked = 0

    async def ask(self, messages: Any, *, on_text: Any, **_: Any) -> dict[str, Any]:
        self.asked += 1
        self.record += [{"tool": t, "args": {}} for t in self.tools]
        for piece in self.pieces:
            await on_text(piece)
        return {
            "status": "ok",
            "text": "".join(self.pieces),
            "first_call_s": 0,
            "first_text_s": 0,
            "end_s": 0,
        }

    async def cancel(self) -> None:
        return None


async def turn(server: AgentServer, messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(ws_server.sockets)).getsockname()[1]}"
    events: list[dict[str, Any]] = []
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": messages}))
        while not events or "done" not in events[-1]:
            events.append(json.loads(await ws.recv()))
    ws_server.close()
    return events


async def test_over_the_socket_a_restated_status_sentence_and_a_premature_claim_never_arrive(
    tmp_path: Path,
) -> None:
    record: list[dict[str, Any]] = []
    pieces = [
        "I am updating the restaurant website ",
        "to include the genre. OK, I am updating the website with the genre. ",
        "I've added that update to your page.",
    ]
    agent = Agent(record, pieces, ["delegate"])
    server = AgentServer(agent, record, tmp_path / "log.jsonl")  # type: ignore[arg-type]
    events = await turn(server, [{"role": "user", "content": "add the genre to the website"}])
    spoken = "".join(str(e.get("text", "")) for e in events)
    assert spoken.strip() == "I am updating the restaurant website to include the genre."
    row = json.loads((tmp_path / "log.jsonl").read_text())
    assert len(row["dropped_sentences"]) == 2 and "added" not in row["text"]


async def test_over_the_socket_a_backchannel_starts_no_model_call(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = Agent(record, ["Looking up Boss."], ["web_research"])
    server = AgentServer(agent, record, tmp_path / "log.jsonl")  # type: ignore[arg-type]
    history = [
        {"role": "user", "content": "tell me about the festival"},
        {"role": "assistant", "content": "The festival is in January."},
        {"role": "user", "content": "Mm-hmm."},
    ]
    assert await turn(server, history) == [{"done": True}]
    assert agent.asked == 0
    offer = [
        {"role": "user", "content": "open the page"},
        {"role": "assistant", "content": "Would you like me to open it for you?"},
        {"role": "user", "content": "Mm-hmm."},
    ]
    await turn(server, offer)
    assert agent.asked == 1  # after an offer "uh-huh" may mean yes: the model sees it


async def test_a_restated_request_cancels_the_job_started_seconds_ago(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled: list[str] = []

    async def cancel(params: Any) -> None:
        cancelled.append(params.arguments["job"])
        await params.result_callback(
            json.dumps({"job": params.arguments["job"], "status": "cancelled"})
        )

    monkeypatch.setattr(agent_tools, "_cancel_job", [cancel])
    monkeypatch.setattr(agent_tools, "_last_job", [("job7", time.monotonic() - 5)])
    agent_tools.question.current = "Sorry, I mean, could you make a page about X instead"
    assert await agent_tools.replace_running_job(agent_tools.question.current) is True
    assert cancelled == ["job7"] and agent_tools._last_job == []

    monkeypatch.setattr(agent_tools, "_last_job", [("job8", time.monotonic() - 5)])
    assert await agent_tools.replace_running_job("make a page about Y") is False  # no repair marker
    monkeypatch.setattr(agent_tools, "_last_job", [("job9", time.monotonic() - 90)])
    assert await agent_tools.replace_running_job("no, I mean Z") is False  # too long ago
    assert cancelled == ["job7"]
    agent_tools.question.current = ""


async def test_a_job_that_already_finished_is_not_called_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancel(params: Any) -> None:
        await params.result_callback(json.dumps({"status": "none", "message": "no running task"}))

    monkeypatch.setattr(agent_tools, "_cancel_job", [cancel])
    monkeypatch.setattr(agent_tools, "_last_job", [("job1", time.monotonic())])
    assert await agent_tools.replace_running_job("sorry, I mean the other one") is False
    await asyncio.sleep(0)
