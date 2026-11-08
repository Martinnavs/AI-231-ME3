"""The text-only addressee gate and its --addressee wiring."""

from __future__ import annotations

import argparse
import io
import json
import time
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from doc_chat.addressee import decide  # noqa: E402
from doc_chat.chain import Answer  # noqa: E402
from doc_chat.dialogue import ChatHistory  # noqa: E402
from doc_chat.speech import HeardTurn  # noqa: E402


def label(text: str, since: float | None = None, pending: bool = False, names: Any = ()) -> str:
    return decide(text, since_reply_s=since, pending=pending, names=names).label


@pytest.mark.parametrize(
    "text",
    [
        "Play some jazz.",
        "Can you help me?",
        "Would you mind?",
        "Water the plants, please.",
        "Who is Cormac McCarthy?",
        "How tall is Everest?",
        "Is the light on",
        "Set a timer",
    ],
)
def test_addressed_cues(text: str) -> None:
    assert label(text) == "addressed"


def test_name_in_names_beats_the_vocative_and_only_then() -> None:
    assert label("Jarvis, the sky is blue today.", names=["Jarvis"]) == "addressed"
    assert label("Jarvis, the sky is blue today.") == "ignore"


def test_indirect_request_is_not_ignored() -> None:
    assert label("I want to hear some jazz.") != "ignore"


def test_short_turn_after_a_recent_reply_is_a_followup() -> None:
    assert label("Ten minutes.", since=3.0) == "addressed"
    assert label("Mm", since=3.0) == "addressed"
    assert label("Ten minutes.", since=30.0) == "unsure"
    assert label("Ten minutes.", since=None) == "unsure"
    assert label("the sky is blue and so is the sea today", since=3.0) != "addressed"


def test_pending_is_always_addressed() -> None:
    for text in ["Dad, give me a hand", "I'm going to take a nap.", "Hey Jamie, yeah?"]:
        assert label(text, pending=True) == "addressed"


@pytest.mark.parametrize(
    "text",
    ["Jamie, you fed the dog already, yeah?", "Hey Jamie, you fed the dog?", "Dr. Patel, I'm out."],
)
def test_leading_vocative_is_ignored(text: str) -> None:
    v = decide(text, since_reply_s=None, pending=False)
    assert v.label == "ignore" and v.reasons == ("vocative",)


def test_address_term_first_is_ignored() -> None:
    v = decide("Dad the car is making noise", since_reply_s=None, pending=False)
    assert v.label == "ignore" and v.reasons == ("address_term",)


@pytest.mark.parametrize(
    "text", ["I'm going to take a nap.", "The doorbell just rang.", "Your coffee's getting cold."]
)
def test_side_declarative_is_ignored(text: str) -> None:
    v = decide(text, since_reply_s=None, pending=False, declaratives=True)
    assert v.label == "ignore" and v.reasons == ("declarative",)
    assert decide(text, since_reply_s=None, pending=False).label == "unsure"  # off by default


def test_vocative_followed_by_a_command_is_unsure_not_ignored() -> None:
    assert label("Mateo, play something nice") == "unsure"
    assert label("Roomie, can you pass the salt") != "ignore"


def test_discourse_openers_are_not_names() -> None:
    assert label("Well, I think so.") != "ignore"
    assert label("Oh, what is the capital of?") == "addressed"


def test_noun_phrases_and_question_marks_are_not_side_declaratives() -> None:
    assert label("The Pulitzer Prize") == "unsure"
    assert label("The doorbell just rang?") == "unsure"
    assert label("I see.") == "unsure"


@pytest.mark.parametrize("text", ["Turn on the lights.", "Who is Cormac McCarthy?", "", "   "])
def test_real_requests_are_never_ignored(text: str) -> None:
    assert label(text) != "ignore"


def test_cue_and_side_cue_together_is_unsure() -> None:
    v = decide("Mia, dinner's ready", since_reply_s=3.0, pending=False)
    assert v.label == "unsure" and "followup" in v.reasons


class FakeClient:
    tts_sample_rate = 24000

    async def speak_text(self, text: str, voice: str | None = None, **kw: Any) -> Any:
        return b"\x00\x00" * 10, 5.0, 9.0, 5.0


class FakeChain:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def ainvoke(self, question: str) -> Answer:
        self.questions.append(question)
        return Answer(text="ok", source=None, retrieval_ms=0.0, llm_ms=1.0)


def heard(text: str) -> HeardTurn:
    now = time.perf_counter()
    return HeardTurn(text, None, None, now - 0.5, now, now - 0.1)


def make_session(**extra: Any) -> Any:
    from doc_chat.__main__ import Session

    args = argparse.Namespace(play=False, voice="F1", slot="doc?", risk=None, **extra)
    chain = FakeChain()
    log = io.StringIO()
    s = Session(
        args=args,
        client=FakeClient(),  # type: ignore[arg-type]
        log_file=log,
        out_dir=None,
        chain=chain,
        history=ChatHistory(4),
    )
    return s, chain, log


SIDE = "Dad, give me a hand with this math real quick."
SIDE_UNSURE_FREE = "Hey Jamie, you fed the dog already, yeah?"


async def test_off_changes_nothing() -> None:
    s, chain, log = make_session(addressee="off")
    await s.handle(1, heard(SIDE_UNSURE_FREE))
    assert chain.questions == [SIDE_UNSURE_FREE]
    assert "addressee" not in json.loads(log.getvalue())


async def test_missing_flag_is_off() -> None:
    s, chain, log = make_session()
    await s.handle(1, heard(SIDE_UNSURE_FREE))
    assert chain.questions == [SIDE_UNSURE_FREE]
    assert "addressee" not in json.loads(log.getvalue())


async def test_log_records_the_verdict_but_still_answers() -> None:
    s, chain, log = make_session(addressee="log", addressee_names="")
    await s.handle(1, heard(SIDE_UNSURE_FREE))
    rec = json.loads(log.getvalue())
    assert chain.questions == [SIDE_UNSURE_FREE]
    assert rec["addressee"] == "ignore" and rec["addressee_reasons"] == ["vocative"]


async def test_on_drops_an_ignore_verdict_and_logs_it(capsys: pytest.CaptureFixture[str]) -> None:
    s, chain, log = make_session(addressee="on", addressee_names="")
    await s.handle(1, heard(SIDE_UNSURE_FREE))
    assert chain.questions == []
    rec = json.loads(log.getvalue())
    assert rec["question"] == SIDE_UNSURE_FREE and rec["answer_words"] == 0
    assert rec["addressee"] == "ignore" and rec["addressee_reasons"] == ["vocative"]
    assert "(ignored: not addressed to me)" in capsys.readouterr().out


async def test_on_still_answers_a_real_request_and_tracks_the_reply_time() -> None:
    s, chain, log = make_session(addressee="on", addressee_names="")
    assert s._reply_end is None
    await s.handle(1, heard("Turn on the lights."))
    assert chain.questions == ["Turn on the lights."]
    assert s._reply_end is not None
    await s.handle(2, heard("Ten minutes."))
    assert chain.questions[-1] == "Ten minutes."
    assert json.loads(log.getvalue().splitlines()[1])["addressee"] == "addressed"


async def test_the_verdict_is_taken_once_so_an_interrupted_turn_cannot_leak_it() -> None:
    s, _chain, _log = make_session(addressee="log", addressee_names="")
    s._addressee_note = {"addressee": "ignore", "addressee_reasons": ["vocative"]}
    assert s._take_addressee_note() == {"addressee": "ignore", "addressee_reasons": ["vocative"]}
    assert s._take_addressee_note() == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("28", 28),
        (" WH-1000XM4 ", "WH-1000XM4"),
        ("pulse", "pulse"),
    ],
)
def test_mic_device_keeps_an_index_numeric_and_a_name_text(value: Any, expected: Any) -> None:
    from doc_chat.__main__ import mic_device

    assert mic_device(value) == expected
