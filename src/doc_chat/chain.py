"""Retrieval and answer chain for voice doc-chat (ticket 24)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import Runnable, RunnableLambda

from doc_chat.dialogue import history_messages
from doc_chat.qwen import describe_failure
from lan_speech.protocol import split_sentences

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


@dataclass(frozen=True)
class Answer:
    text: str
    source: str | None
    retrieval_ms: float
    llm_ms: float | None = None
    llm_error: str | None = None


_NO_HIT_TEXT = "I couldn't find that in the documents."


def strip_markdown(text: str) -> str:
    """Strip markdown formatting (headings, tables, code blocks, links)."""
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove images
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    # Remove links, keeping link text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove table lines
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.MULTILINE)
    # Remove headings
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate_to_max_words(text: str, max_words: int) -> str:
    """Keep whole sentences up to max_words, or cut at word boundary if first sentence exceeds."""
    text = text.strip()
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text

    sentences = split_sentences(text)

    accumulated: list[str] = []
    current_count = 0
    for s in sentences:
        sw = s.split()
        if current_count + len(sw) <= max_words:
            accumulated.append(s)
            current_count += len(sw)
        else:
            break

    if accumulated:
        return " ".join(accumulated)

    return " ".join(words[:max_words])


_SYSTEM_PROMPT = (
    "You are a voice assistant. Answer the question in at most two short sentences of plain "
    "spoken English: no markdown, lists, or emoji. Use only the context below. If the context "
    "does not contain the answer, say you don't know.\n\nContext:\n{context}"
)

_CHAT_PROMPT = (
    "You are a friendly voice assistant. Answer in at most two short sentences of plain spoken "
    "English: no markdown, lists, or emoji. If you are not sure, say so briefly."
)

_LLM_DOWN_TEXT = "Sorry, I couldn't reach the language model."
_NO_ANSWER_TEXT = "Sorry, I don't have an answer for that."


def render_system(template: str | None, context: str | None) -> str:
    """Fill {context} without treating other braces as template variables."""
    if context is None:
        return (template or _CHAT_PROMPT).replace("{context}", "").strip()
    text = template or _SYSTEM_PROMPT
    if "{context}" not in text:
        text = f"{text}\n\nContext:\n{{context}}"
    return text.replace("{context}", context)


HistoryFn = Callable[[], list[tuple[str, str]]]

_SHORT_FOLLOW_UP_WORDS = 5


def retrieval_query(question: str, history: list[tuple[str, str]]) -> str:
    """The text to search the documents with.

    A short follow-up ("and her sister?") says nothing on its own, so the previous question is
    searched with it.
    """
    if history and len(question.split()) <= _SHORT_FOLLOW_UP_WORDS:
        return f"{history[-1][0]} {question}"
    return question


def _messages(
    system: str, question: str, history: list[tuple[str, str]] | None = None
) -> list[Any]:
    from langchain_core.messages import HumanMessage, SystemMessage

    return [
        SystemMessage(content=system),
        *history_messages(history or []),
        HumanMessage(content=question),
    ]


def clean_llm_reply(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"^[\s\S]*?</think>", "", text)
    return strip_markdown(text)


def _visible_so_far(raw: str) -> str:
    """Reply text so far with finished and still-open <think> blocks removed."""
    raw = re.sub(r"<think>[\s\S]*?</think>", "", raw)
    raw = re.sub(r"^[\s\S]*?</think>", "", raw)
    open_at = raw.find("<think>")
    return raw if open_at == -1 else raw[:open_at]


def _ask_llm(
    llm: BaseChatModel, system: str, question: str, history: list[tuple[str, str]] | None = None
) -> str:
    reply = llm.invoke(_messages(system, question, history))
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    return clean_llm_reply(content)


def build_chat_chain(
    llm: BaseChatModel,
    *,
    max_words: int = 40,
    system_prompt: str | None = None,
    history: HistoryFn | None = None,
) -> Runnable[str, Answer]:
    """Chat with no documents; with ``history`` the earlier turns go to the model too."""

    def _chat_fn(question: str) -> Answer:
        t0 = time.perf_counter()
        try:
            reply = _ask_llm(
                llm, render_system(system_prompt, None), question, history() if history else None
            )
        except Exception as exc:
            return Answer(
                text=_LLM_DOWN_TEXT,
                source=None,
                retrieval_ms=0.0,
                llm_ms=(time.perf_counter() - t0) * 1000.0,
                llm_error=describe_failure(exc),
            )
        text = truncate_to_max_words(reply, max_words) or _LLM_DOWN_TEXT
        return Answer(
            text=text, source=None, retrieval_ms=0.0, llm_ms=(time.perf_counter() - t0) * 1000.0
        )

    return RunnableLambda(_chat_fn)


def _retrieve(retriever: Any, question: str) -> tuple[list[Any], float]:
    t0 = time.perf_counter()
    if hasattr(retriever, "invoke"):
        docs = retriever.invoke(question)
    else:
        docs = retriever.get_relevant_documents(question)
    return docs, (time.perf_counter() - t0) * 1000.0


def _doc_text(doc: Any) -> str:
    return strip_markdown(doc.page_content if hasattr(doc, "page_content") else str(doc))


def build_answer_chain(
    retriever: Any,
    *,
    max_words: int = 40,
    llm: BaseChatModel | None = None,
    system_prompt: str | None = None,
    history: HistoryFn | None = None,
) -> Runnable[str, Answer]:
    """Build a Runnable that takes a question string and returns an Answer dataclass."""

    def _answer_fn(question: str) -> Answer:
        past = history() if history else []
        docs, retrieval_ms = _retrieve(retriever, retrieval_query(question, past))
        if not docs:
            return Answer(text=_NO_HIT_TEXT, source=None, retrieval_ms=retrieval_ms)

        top_doc = docs[0]
        source = top_doc.metadata.get("source") if hasattr(top_doc, "metadata") else None
        passage = _doc_text(top_doc)

        if llm is None:
            if not passage:
                return Answer(text=_NO_HIT_TEXT, source=None, retrieval_ms=retrieval_ms)
            return Answer(
                text=truncate_to_max_words(passage, max_words),
                source=source,
                retrieval_ms=retrieval_ms,
            )
        context = "\n\n".join(_doc_text(d) for d in docs)
        t1 = time.perf_counter()
        try:
            reply = _ask_llm(llm, render_system(system_prompt, context), question, past)
        except Exception as exc:
            return Answer(
                text=truncate_to_max_words(passage, max_words) or _NO_HIT_TEXT,
                source=source,
                retrieval_ms=retrieval_ms,
                llm_ms=(time.perf_counter() - t1) * 1000.0,
                llm_error=describe_failure(exc),
            )
        llm_ms = (time.perf_counter() - t1) * 1000.0
        text = truncate_to_max_words(reply, max_words) or _NO_HIT_TEXT
        return Answer(text=text, source=source, retrieval_ms=retrieval_ms, llm_ms=llm_ms)

    return RunnableLambda(_answer_fn)


class SentenceStream:
    """Qwen's reply as it arrives, one finished sentence at a time.

    A sentence is released once the next one starts, so "3." followed by "5" is never split.
    Afterwards `text`, `first_token_ms`, `llm_ms` and `error` describe what happened.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        question: str,
        *,
        context: str | None,
        source: str | None,
        retrieval_ms: float,
        fallback: str,
        max_words: int,
        system_prompt: str | None,
        history: list[tuple[str, str]] | None = None,
    ) -> None:
        self.source = source
        self.retrieval_ms = retrieval_ms
        self.first_token_ms: float | None = None
        self.llm_ms: float | None = None
        self.error: str | None = None
        self.text = ""
        self._llm = llm
        self._question = question
        self._system = render_system(system_prompt, context)
        self._fallback = fallback
        self._max_words = max_words
        self._history = history

    def __iter__(self) -> Iterator[str]:
        t0 = time.perf_counter()
        raw = ""
        released: list[str] = []
        consumed = 0
        words = 0
        stopped = False
        try:
            for chunk in self._llm.stream(_messages(self._system, self._question, self._history)):
                piece = chunk.content if isinstance(chunk.content, str) else ""
                if piece and self.first_token_ms is None:
                    self.first_token_ms = (time.perf_counter() - t0) * 1000.0
                raw += piece
                ready = split_sentences(_visible_so_far(raw))[:-1]
                for sentence in ready[consumed:]:
                    consumed += 1
                    out = self._take(strip_markdown(sentence), words)
                    if out is None:
                        stopped = True
                        break
                    released.append(out)
                    words += len(out.split())
                    yield out
                if stopped:
                    break
            if not stopped:
                for sentence in split_sentences(_visible_so_far(raw))[consumed:]:
                    out = self._take(strip_markdown(sentence), words)
                    if out is None:
                        break
                    released.append(out)
                    words += len(out.split())
                    yield out
        except Exception as exc:
            self.error = describe_failure(exc)
        if not released:
            fallback = self._fallback if self.error else _NO_ANSWER_TEXT
            released.append(fallback)
            self._finish(t0, released)
            yield fallback
            return
        self._finish(t0, released)

    def _take(self, sentence: str, words_so_far: int) -> str | None:
        n = len(sentence.split())
        if not sentence or (words_so_far and words_so_far + n > self._max_words):
            return None
        return " ".join(sentence.split()[: self._max_words]) if not words_so_far else sentence

    def _finish(self, t0: float, released: list[str]) -> None:
        self.llm_ms = (time.perf_counter() - t0) * 1000.0
        self.text = " ".join(released)


def build_sentence_streamer(
    retriever: Any | None,
    llm: BaseChatModel,
    *,
    max_words: int = 40,
    system_prompt: str | None = None,
    history: HistoryFn | None = None,
) -> Callable[[str], SentenceStream]:
    """Like the answer chains, but returns a SentenceStream so speech can start early."""

    def _start(question: str) -> SentenceStream:
        past = history() if history else []
        if retriever is None:
            return SentenceStream(
                llm,
                question,
                context=None,
                source=None,
                retrieval_ms=0.0,
                fallback=_LLM_DOWN_TEXT,
                max_words=max_words,
                system_prompt=system_prompt,
                history=past,
            )
        docs, retrieval_ms = _retrieve(retriever, retrieval_query(question, past))
        passage = _doc_text(docs[0]) if docs else ""
        source = docs[0].metadata.get("source") if docs and hasattr(docs[0], "metadata") else None
        return SentenceStream(
            llm,
            question,
            context="\n\n".join(_doc_text(d) for d in docs) if docs else "",
            source=source,
            retrieval_ms=retrieval_ms,
            fallback=truncate_to_max_words(passage, max_words) or _NO_HIT_TEXT,
            max_words=max_words,
            system_prompt=system_prompt,
            history=past,
        )

    return _start
