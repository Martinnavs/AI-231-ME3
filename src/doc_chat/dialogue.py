"""Conversation state for the voice loop: what was said before, and clarifying questions.

Two small pieces, both plain Python:

* :class:`ChatHistory` keeps the last few question/answer pairs so a follow-up ("and her sister?")
  reaches the model with its context.
* :func:`clarification_for` turns what the speech server says about a transcript (its ``repair``
  summary) into something to ask the user instead of guessing, and :func:`parse_yes_no` reads the
  reply.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

_YES = frozenset(
    [
        "yes",
        "yeah",
        "yep",
        "yup",
        "sure",
        "correct",
        "right",
        "exactly",
        "absolutely",
        "definitely",
        "please",
        "do",
        "okay",
        "ok",
        "affirmative",
        "indeed",
    ]
)
_NO = frozenset(["no", "nope", "nah", "wrong", "incorrect", "negative", "not"])


class ChatHistory:
    """The last ``max_turns`` question/answer pairs, oldest first."""

    def __init__(self, max_turns: int = 4) -> None:
        self.max_turns = max_turns
        self._turns: list[tuple[str, str]] = []

    def add(self, question: str, answer: str) -> None:
        if self.max_turns <= 0 or not question.strip() or not answer.strip():
            return
        self._turns.append((question.strip(), answer.strip()))
        del self._turns[: -self.max_turns]

    def pairs(self) -> list[tuple[str, str]]:
        """A copy, so a request in flight is not changed by the next turn."""
        return list(self._turns)

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)


def parse_yes_no(text: str) -> Literal["yes", "no"] | None:
    """Read a short reply to a yes/no question. Anything longer or unrelated is not an answer."""
    words = re.sub(r"[^a-z' ]", " ", text.lower()).split()
    if not words or len(words) > 4:
        return None
    has_no = any(w in _NO for w in words)
    has_yes = any(w in _YES for w in words)
    if has_no and not has_yes:
        return "no"
    if has_yes and not has_no:
        return "yes"
    return None


@dataclass(frozen=True)
class Clarification:
    """Something to say instead of answering.

    ``kind`` is "confirm" (``if_yes`` is the question with the guessed name filled in) or "ask"
    (a question with no candidate, such as "Which contact was that?" or "I didn't catch that").
    """

    kind: Literal["confirm", "ask"]
    question: str
    if_yes: str | None = None


def apply_match(text: str, heard: str, entity: str) -> str:
    """Replace the words ``heard`` (as the repair step saw them) with ``entity`` inside ``text``."""
    target = heard.lower().split()
    tokens = text.split()
    norm = [re.sub(r"[^a-z0-9']", "", t.lower()) for t in tokens]
    n = len(target)
    for i in range(len(tokens) - n + 1):
        if norm[i : i + n] == target:
            trailing = re.search(r"[^\w']+$", tokens[i + n - 1])
            return " ".join(
                [*tokens[:i], entity + (trailing.group(0) if trailing else ""), *tokens[i + n :]]
            )
    return text


def clarification_for(text: str, repair: dict[str, Any] | None) -> Clarification | None:
    """What to ask given a transcript and the server's ``repair`` summary, or None to answer."""
    if not repair:
        return None
    rung = repair.get("rung")
    question = repair.get("question")
    if rung == "confirm" and question:
        filled = text
        for m in repair.get("matches", []):
            if m.get("rung") == "confirm":
                filled = apply_match(filled, str(m["heard"]), str(m["entity"]))
        return Clarification("confirm", str(question), filled)
    if rung in ("targeted", "reprompt") and question:
        return Clarification("ask", str(question))
    return None


def history_messages(history: Sequence[tuple[str, str]]) -> list[Any]:
    """The history as chat messages, for a model call."""
    from langchain_core.messages import AIMessage, HumanMessage

    out: list[Any] = []
    for question, answer in history:
        out.append(HumanMessage(content=question))
        out.append(AIMessage(content=answer))
    return out
