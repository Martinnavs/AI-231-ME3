"""Qwen request shape and reply handling against a local fake OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

pytest.importorskip("langchain_openai")
pytest.importorskip("langchain_core")

from langchain_core.documents import Document  # noqa: E402

from doc_chat.chain import build_answer_chain  # noqa: E402
from doc_chat.qwen import make_chat_model  # noqa: E402

REPLY = "<think>plan</think>**Harry** is a young wizard. He attends Hogwarts."


class _Retriever:
    def invoke(self, question: str) -> list[Document]:
        return [Document(page_content="# Harry\n\nHarry is a wizard.", metadata={"source": "h.md"})]


def test_one_shot_request_shape_and_reply_cleanup() -> None:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), **body})
            payload = json.dumps(
                {
                    "id": "x",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": REPLY},
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        llm = make_chat_model(f"http://127.0.0.1:{port}/v1", "test-key", "fake/Qwen")
        chain = build_answer_chain(_Retriever(), max_words=40, llm=llm)
        first = chain.invoke("Who is Harry?")
        second = chain.invoke("And where does he study?")
    finally:
        httpd.shutdown()

    assert first.text == "Harry is a young wizard. He attends Hogwarts."
    assert first.llm_error is None and first.llm_ms is not None
    assert len(seen) == 2
    for req, question in zip(seen, ["Who is Harry?", "And where does he study?"], strict=True):
        assert req["path"] == "/v1/chat/completions"
        assert req["auth"] == "Bearer test-key"
        assert req["model"] == "fake/Qwen"
        assert req["chat_template_kwargs"] == {"enable_thinking": False}
        assert [m["role"] for m in req["messages"]] == ["system", "user"]
        assert req["messages"][1]["content"] == question
        assert "Harry is a wizard." in req["messages"][0]["content"]
        assert "#" not in req["messages"][0]["content"]
    assert second.llm_error is None
