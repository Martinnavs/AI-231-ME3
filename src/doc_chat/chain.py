"""Retrieval and answer chain for voice doc-chat (ticket 24)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import Runnable, RunnableLambda

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


def clean_llm_reply(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"^[\s\S]*?</think>", "", text)
    return strip_markdown(text)


def _ask_llm(llm: BaseChatModel, context: str | None, question: str) -> str:
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    system = _CHAT_PROMPT if context is None else _SYSTEM_PROMPT
    prompt = ChatPromptTemplate.from_messages([("system", system), ("user", "{question}")])
    variables = (
        {"question": question} if context is None else {"context": context, "question": question}
    )
    reply = (prompt | llm | StrOutputParser()).invoke(variables)
    return clean_llm_reply(str(reply))


_LLM_DOWN_TEXT = "Sorry, I couldn't reach the language model."


def build_chat_chain(llm: BaseChatModel, *, max_words: int = 40) -> Runnable[str, Answer]:
    """One-shot chat with no documents and no history."""

    def _chat_fn(question: str) -> Answer:
        t0 = time.perf_counter()
        try:
            reply = _ask_llm(llm, None, question)
        except Exception as exc:
            return Answer(
                text=_LLM_DOWN_TEXT,
                source=None,
                retrieval_ms=0.0,
                llm_ms=(time.perf_counter() - t0) * 1000.0,
                llm_error=type(exc).__name__,
            )
        text = truncate_to_max_words(reply, max_words) or _LLM_DOWN_TEXT
        return Answer(
            text=text, source=None, retrieval_ms=0.0, llm_ms=(time.perf_counter() - t0) * 1000.0
        )

    return RunnableLambda(_chat_fn)


def build_answer_chain(
    retriever: Any,
    *,
    max_words: int = 40,
    llm: BaseChatModel | None = None,
) -> Runnable[str, Answer]:
    """Build a Runnable that takes a question string and returns an Answer dataclass."""

    def _answer_fn(question: str) -> Answer:
        t0 = time.perf_counter()
        if hasattr(retriever, "invoke"):
            docs = retriever.invoke(question)
        else:
            docs = retriever.get_relevant_documents(question)
        retrieval_ms = (time.perf_counter() - t0) * 1000.0

        if not docs:
            return Answer(text=_NO_HIT_TEXT, source=None, retrieval_ms=retrieval_ms)

        top_doc = docs[0]
        source = top_doc.metadata.get("source") if hasattr(top_doc, "metadata") else None

        if llm is None:
            raw_text = top_doc.page_content if hasattr(top_doc, "page_content") else str(top_doc)
            cleaned = strip_markdown(raw_text)
            if not cleaned:
                return Answer(text=_NO_HIT_TEXT, source=None, retrieval_ms=retrieval_ms)
            ans_text = truncate_to_max_words(cleaned, max_words)
            return Answer(text=ans_text, source=source, retrieval_ms=retrieval_ms)
        context = "\n\n".join(
            strip_markdown(d.page_content if hasattr(d, "page_content") else str(d)) for d in docs
        )
        t1 = time.perf_counter()
        try:
            reply = _ask_llm(llm, context, question)
        except Exception as exc:
            fallback = truncate_to_max_words(strip_markdown(top_doc.page_content), max_words)
            return Answer(
                text=fallback or _NO_HIT_TEXT,
                source=source,
                retrieval_ms=retrieval_ms,
                llm_ms=(time.perf_counter() - t1) * 1000.0,
                llm_error=type(exc).__name__,
            )
        llm_ms = (time.perf_counter() - t1) * 1000.0
        text = truncate_to_max_words(reply, max_words) or _NO_HIT_TEXT
        return Answer(text=text, source=source, retrieval_ms=retrieval_ms, llm_ms=llm_ms)

    return RunnableLambda(_answer_fn)
