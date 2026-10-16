"""Retrieval and answer chain for voice doc-chat (ticket 24)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import Runnable, RunnableLambda

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


@dataclass(frozen=True)
class Answer:
    text: str
    source: str | None
    retrieval_ms: float


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

    raw_sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in raw_sentences if s.strip()]

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
        else:
            from langchain_core.output_parsers import StrOutputParser
            from langchain_core.prompts import ChatPromptTemplate

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "Answer in <= 2 short sentences using only this context:\n\n{context}",
                    ),
                    ("user", "{question}"),
                ]
            )
            context = "\n\n".join(
                d.page_content if hasattr(d, "page_content") else str(d) for d in docs
            )
            llm_chain = prompt | llm | StrOutputParser()
            output = llm_chain.invoke({"context": context, "question": question})
            ans_str = str(output).strip()
            return Answer(text=ans_str, source=source, retrieval_ms=retrieval_ms)

    return RunnableLambda(_answer_fn)
