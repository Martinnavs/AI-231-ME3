"""History, yes/no replies and clarifying questions for the voice loop."""

from __future__ import annotations

import pytest

from doc_chat.dialogue import (
    ChatHistory,
    Clarification,
    apply_match,
    clarification_for,
    history_messages,
    parse_yes_no,
)


def test_history_keeps_the_most_recent_pairs_in_order() -> None:
    h = ChatHistory(max_turns=2)
    for i in range(4):
        h.add(f"q{i}", f"a{i}")
    assert h.pairs() == [("q2", "a2"), ("q3", "a3")]


def test_history_ignores_empty_turns_and_can_be_switched_off() -> None:
    h = ChatHistory(max_turns=3)
    h.add("", "answer")
    h.add("question", "  ")
    assert len(h) == 0
    off = ChatHistory(max_turns=0)
    off.add("q", "a")
    assert off.pairs() == []


def test_pairs_is_a_copy() -> None:
    h = ChatHistory()
    h.add("q", "a")
    snapshot = h.pairs()
    h.add("q2", "a2")
    assert snapshot == [("q", "a")]


def test_history_becomes_alternating_chat_messages() -> None:
    msgs = history_messages([("who is he", "a wizard"), ("and her", "a witch")])
    assert [type(m).__name__ for m in msgs] == ["HumanMessage", "AIMessage"] * 2
    assert msgs[1].content == "a wizard"


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        ("Yes.", "yes"),
        ("yeah that's right", "yes"),
        ("No", "no"),
        ("nope, wrong", "no"),
        ("no no no", "no"),
        ("yes and no", None),
        ("Tell me about the Triwizard Tournament", None),
        ("", None),
        ("I think it was the other one", None),
    ],
)
def test_short_replies_are_read_as_yes_or_no(heard: str, expected: str | None) -> None:
    assert parse_yes_no(heard) == expected


def test_the_guessed_name_replaces_what_was_heard() -> None:
    assert apply_match("Is dumbledor a wizard?", "dumbledor", "Dumbledore") == (
        "Is Dumbledore a wizard?"
    )
    assert apply_match("who is hermoine granger", "hermoine granger", "Hermione Granger") == (
        "who is Hermione Granger"
    )
    assert apply_match("nothing to change", "dumbledor", "Dumbledore") == "nothing to change"


def test_a_confirm_rung_becomes_a_yes_no_question_with_the_answer_filled_in() -> None:
    repair = {
        "rung": "confirm",
        "question": "Did you mean Dumbledore?",
        "matches": [{"heard": "dumbledor", "entity": "Dumbledore", "rung": "confirm"}],
    }
    c = clarification_for("Is dumbledor a wizard?", repair)
    assert c == Clarification("confirm", "Did you mean Dumbledore?", "Is Dumbledore a wizard?")


def test_a_targeted_or_reprompt_rung_is_a_plain_question() -> None:
    for rung in ("targeted", "reprompt"):
        c = clarification_for("call", {"rung": rung, "question": "Which contact was that?"})
        assert c == Clarification("ask", "Which contact was that?")


def test_silent_and_implicit_repairs_need_no_question() -> None:
    assert clarification_for("hi", {"rung": "silent", "question": None}) is None
    assert clarification_for("hi", {"rung": "implicit", "question": None}) is None
    assert clarification_for("hi", None) is None
