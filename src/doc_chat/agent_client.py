"""Chat model backed by the Pipecat agent server in pipeline/ (voice_pipeline.agent_server).

The server owns the system prompt, the tools (smart_home, the docs MCP server) and the LLM; this
client sends the conversation and streams the reply text back. Closing the socket before the reply
is done (the listener interrupted) tells the server to cancel it.

Protocol, one request per WebSocket connection:
  client -> {"messages": [{"role": "system"|"user"|"assistant", "content": str}, ...]}
  server -> {"text": str} ... then {"done": true}, or {"error": str}; and
            {"event": "tool", "name": str, "said": bool} whenever a tool is called (for a
            filler; "said" = the model's own sentence for the call was already sent);
            {"text": str, "flush": true} is a sentence to speak now, not held back for the next;
            {"event": "text"} once, when the first text other than a flush sentence arrives
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from websockets.sync.client import connect

from doc_chat.chain import FLUSH

DEFAULT_URL = "ws://127.0.0.1:8781"
_ROLES = {"system": "system", "human": "user", "ai": "assistant"}


def to_wire(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """LangChain messages as the server's {"role", "content"} dicts."""
    return [
        {"role": _ROLES[m.type], "content": str(m.content)} for m in messages if m.type in _ROLES
    ]


class AgentChatModel(BaseChatModel):
    """Streams replies from the agent server; see the module docstring for the protocol."""

    url: str = DEFAULT_URL
    timeout: float = 30.0
    on_event: Any = None  # called with every {"event": ...} message (worker thread)

    @property
    def _llm_type(self) -> str:
        return "agent"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = "".join(str(chunk.message.content) for chunk in self._stream(messages))
        text = text.replace(FLUSH, "")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        with connect(self.url, open_timeout=self.timeout) as ws:
            ws.send(json.dumps({"messages": to_wire(messages)}))
            first = True
            while True:
                event = json.loads(ws.recv(timeout=self.timeout))
                if "text" in event:
                    if first and not event.get("flush") and self.on_event is not None:
                        self.on_event({"event": "text"})  # the `say` is not the answer
                    first = first and bool(event.get("flush"))
                    text = event["text"] + (FLUSH if event.get("flush") else "")
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text))
                elif "event" in event:
                    if self.on_event is not None:
                        self.on_event(event)
                elif event.get("done"):
                    return
                else:
                    raise RuntimeError(f"agent server error: {event.get('error', 'unknown')}")
