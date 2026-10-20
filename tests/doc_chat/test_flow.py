"""The improved loop: clarifying questions, yes/no replies and history, through a real Session."""

from __future__ import annotations

import argparse
import io
import time
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from doc_chat.__main__ import Session, timing_line  # noqa: E402
from doc_chat.chain import Answer  # noqa: E402
from doc_chat.dialogue import ChatHistory  # noqa: E402
from doc_chat.speech import HeardTurn  # noqa: E402


class FakeClient:
    tts_sample_rate = 24000

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak_text(self, text: str, voice: str | None = None, **kw: Any) -> Any:
        self.spoken.append(text)
        return b"\x00\x00" * 10, 5.0, 9.0, 5.0


class FakeChain:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def ainvoke(self, question: str) -> Answer:
        self.questions.append(question)
        return Answer(text=f"Answer to {question}", source=None, retrieval_ms=0.0, llm_ms=1.0)


def heard(text: str, repair: dict[str, Any] | None = None, raw: str | None = None) -> HeardTurn:
    now = time.perf_counter()
    return HeardTurn(text, raw, repair, now - 0.5, now, now - 0.1)


CONFIRM = {
    "rung": "confirm",
    "question": "Did you mean Dumbledore?",
    "matches": [{"heard": "dumbledor", "entity": "Dumbledore", "rung": "confirm"}],
}


def session() -> tuple[Session, FakeClient, FakeChain]:
    client, chain = FakeClient(), FakeChain()
    args = argparse.Namespace(play=False, voice="F1", slot="doc?", risk=None)
    s = Session(
        args=args,
        client=client,  # type: ignore[arg-type]
        log_file=io.StringIO(),
        out_dir=None,
        chain=chain,
        history=ChatHistory(4),
    )
    return s, client, chain


async def test_a_plain_turn_is_answered_and_remembered() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Who is Harry?"))
    assert chain.questions == ["Who is Harry?"]
    assert client.spoken == ["Answer to Who is Harry?"]
    assert s.history.pairs() == [("Who is Harry?", "Answer to Who is Harry?")]


async def test_a_doubtful_name_is_asked_about_instead_of_guessed() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    assert client.spoken == ["Did you mean Dumbledore?"]
    assert chain.questions == []  # nothing was sent to the model yet
    assert s.pending is not None and s.pending.if_yes == "Is Dumbledore a wizard?"


async def test_yes_answers_the_corrected_question() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    await s.handle(2, heard("Yes."))
    assert chain.questions == ["Is Dumbledore a wizard?"]
    assert s.pending is None
    assert s.history.pairs()[0][0] == "Is Dumbledore a wizard?"


async def test_no_asks_to_say_it_again_and_calls_nothing() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    await s.handle(2, heard("No."))
    assert client.spoken[-1] == "Okay. Please say it again."
    assert chain.questions == [] and s.pending is None


async def test_a_rephrased_question_is_taken_as_a_new_one() -> None:
    s, client, chain = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    await s.handle(2, heard("Tell me about the Triwizard Tournament"))
    assert chain.questions == ["Tell me about the Triwizard Tournament"]
    assert s.pending is None


async def test_a_question_without_a_candidate_is_asked_and_not_remembered() -> None:
    s, client, chain = session()
    repair = {"rung": "targeted", "question": "Which place was that?"}
    await s.handle(1, heard("Weather in", repair))
    assert client.spoken == ["Which place was that?"]
    assert s.pending is None and chain.questions == []


async def test_the_clarification_is_logged_with_the_rung() -> None:
    s, _, _ = session()
    await s.handle(1, heard("Is dumbledor a wizard?", CONFIRM))
    rec = s.records[0]
    assert rec["clarification"] == "Did you mean Dumbledore?" and rec["repair_rung"] == "confirm"
    assert rec["answer_words"] == 0


async def test_the_timing_record_shows_who_waited_for_what() -> None:
    s, _, _ = session()
    await s.handle(1, heard("Who is Harry?"))
    rec = s.records[0]
    assert rec["stt_server_ms"] is not None and rec["pause_ms"] is not None
    assert rec["pause_ms"] > 300  # the client's pause before it asked the server to finish
    assert "pause" in timing_line(rec) and "server" in timing_line(rec)


async def test_what_the_recogniser_heard_is_shown_when_repair_changed_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    s, _, _ = session()
    await s.handle(1, heard("Who is Hermione?", None, raw="Who is hermoine?"))
    assert "recognised as: Who is hermoine?" in capsys.readouterr().out
