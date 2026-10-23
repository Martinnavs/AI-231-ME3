"""Earlier turns reach the model; short follow-ups search with the previous question."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from doc_chat.chain import (  # noqa: E402
    build_answer_chain,
    build_chat_chain,
    build_sentence_streamer,
    retrieval_query,
)
from doc_chat.dialogue import ChatHistory  # noqa: E402


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _Reply:
    def __init__(self, content: str) -> None:
        self.content = content


class RecordingLLM:
    def __init__(self, text: str = "Fine.") -> None:
        self.text = text
        self.calls: list[list[Any]] = []

    def invoke(self, messages: Any) -> _Reply:
        self.calls.append(list(messages))
        return _Reply(self.text)

    def stream(self, messages: Any) -> Iterator[_Chunk]:
        self.calls.append(list(messages))
        yield _Chunk(self.text)


class Retriever:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def invoke(self, query: str) -> list[Any]:
        self.queries.append(query)
        return []


def _roles(messages: list[Any]) -> list[str]:
    return [type(m).__name__.replace("Message", "") for m in messages]


def test_the_chat_chain_sends_earlier_turns_before_the_question() -> None:
    h = ChatHistory()
    h.add("Who is Harry?", "A young wizard.")
    llm = RecordingLLM()
    chain = build_chat_chain(llm, history=h.pairs)  # type: ignore[arg-type]
    chain.invoke("And his friend?")
    assert _roles(llm.calls[0]) == ["System", "Human", "AI", "Human"]
    assert llm.calls[0][1].content == "Who is Harry?"
    assert llm.calls[0][-1].content == "And his friend?"


def test_without_history_the_call_is_unchanged() -> None:
    llm = RecordingLLM()
    build_chat_chain(llm).invoke("Hello?")  # type: ignore[arg-type]
    assert _roles(llm.calls[0]) == ["System", "Human"]


def test_history_is_read_when_the_question_arrives_not_when_the_chain_is_built() -> None:
    h = ChatHistory()
    llm = RecordingLLM()
    chain = build_chat_chain(llm, history=h.pairs)  # type: ignore[arg-type]
    chain.invoke("first")
    h.add("first", "reply")
    chain.invoke("second")
    assert len(llm.calls[0]) == 2 and len(llm.calls[1]) == 4


def test_the_streamer_sends_history_too() -> None:
    h = ChatHistory()
    h.add("Who is Harry?", "A young wizard.")
    llm = RecordingLLM("A witch.")
    stream = build_sentence_streamer(None, llm, history=h.pairs)("And Hermione?")  # type: ignore[arg-type]
    assert list(stream) == ["A witch."]
    assert _roles(llm.calls[0]) == ["System", "Human", "AI", "Human"]


def test_a_short_follow_up_is_searched_with_the_previous_question() -> None:
    assert retrieval_query("and her sister?", [("Who is Petunia?", "x")]) == (
        "Who is Petunia? and her sister?"
    )


def test_a_full_question_is_searched_as_asked() -> None:
    q = "What happened at the Triwizard Tournament final?"
    assert retrieval_query(q, [("Who is Petunia?", "x")]) == q
    assert retrieval_query("and her sister?", []) == "and her sister?"


def test_the_answer_chain_searches_with_the_combined_query() -> None:
    r = Retriever()
    h = ChatHistory()
    h.add("Who is Petunia?", "Harry's aunt.")
    chain = build_answer_chain(r, llm=RecordingLLM(), history=h.pairs)  # type: ignore[arg-type]
    chain.invoke("and her husband?")
    assert r.queries == ["Who is Petunia? and her husband?"]


def test_a_short_question_with_its_own_subject_is_not_glued_to_the_previous_one() -> None:
    # live, 2026-10-23: "Tell me about the road." (5 words) was searched with the Blood Meridian
    # question before it, found Blood Meridian text, and the model said it did not know
    past = [("About Blood Meridian.", "x")]
    for q in ("Tell me about the road.", "Tell me about The Counselor.", "Who played Chigurh?"):
        assert retrieval_query(q, past) == q


def test_short_follow_ups_that_point_back_still_are() -> None:
    past = [("Tell me about Suttree.", "x")]
    assert retrieval_query("When was it published?", past) == (
        "Tell me about Suttree. When was it published?"
    )
    assert retrieval_query("But his sister?", past) == "Tell me about Suttree. But his sister?"
