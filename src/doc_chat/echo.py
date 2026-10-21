"""A stand-in for the language model, for testing speech without one.

It "answers" every question the same way: it repeats what it heard, then reads a fixed passage,
so the reply is long and identical from run to run. That makes talking over it and cutting it off
repeatable, and keeps the model's network time out of the numbers (the first word comes after
``first_token_ms``, a fixed delay).

    python -m doc_chat --mic --play --no-docs --llm echo --stream --record out/sessions/lab
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from doc_chat.phrases import phrases

DEFAULT_PASSAGE = (
    "Here is something to read while you try to interrupt me. "
    "The old library stood at the end of a quiet street, and its windows glowed late into the "
    "evening. Inside, rows of shelves climbed to the ceiling, each one packed with books that "
    "smelled of dust and rain. A small brass bell rang whenever the door opened. "
    "The librarian looked up, nodded once, and went back to her tea. "
    "Outside, the first snow of the year began to fall, softly at first, and then all at once."
)


class EchoChatModel(BaseChatModel):
    """Repeats the last human message, then a fixed passage; no network, no randomness."""

    passage: str = DEFAULT_PASSAGE
    first_token_ms: float = 300.0
    word_delay_ms: float = 20.0
    random_sentences: int = 0  # if > 0: that many random sentences instead of the fixed passage

    @property
    def _llm_type(self) -> str:
        return "echo"

    def _reply(self, messages: list[BaseMessage]) -> str:
        question = next((m.content for m in reversed(messages) if m.type == "human"), "")
        if self.random_sentences > 0:
            # the sentences depend only on the question, so the same question is answered alike
            body = " ".join(phrases(str(question), self.random_sentences))
        else:
            body = self.passage
        return f"I heard you say: {question}. {body}"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        time.sleep(self.first_token_ms / 1000.0)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self._reply(messages)))]
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        time.sleep(self.first_token_ms / 1000.0)
        for word in self._reply(messages).split(" "):
            yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))
            time.sleep(self.word_delay_ms / 1000.0)
