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


def test_chat_without_docs_sends_only_the_question() -> None:
    from doc_chat.chain import build_chat_chain

    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            payload = json.dumps(
                {
                    "id": "x",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "m",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "Paris. It is lovely."},
                        }
                    ],
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
        llm = make_chat_model(f"http://127.0.0.1:{httpd.server_address[1]}/v1", "k", "fake/Qwen")
        chain = build_chat_chain(llm, max_words=40)
        a1 = chain.invoke("What is the capital of France?")
        chain.invoke("And Spain?")
    finally:
        httpd.shutdown()

    assert a1.text == "Paris. It is lovely."
    assert a1.source is None and a1.retrieval_ms == 0.0 and a1.llm_ms is not None
    assert len(seen) == 2
    for req in seen:
        assert [m["role"] for m in req["messages"]] == ["system", "user"]
        assert "context" not in req["messages"][0]["content"].lower()
        assert req["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen[1]["messages"][1]["content"] == "And Spain?"


def test_chat_without_docs_reports_llm_failure() -> None:
    from doc_chat.chain import build_chat_chain

    llm = make_chat_model("http://127.0.0.1:9/v1", "k", "fake/Qwen")
    ans = build_chat_chain(llm).invoke("Hello?")
    assert ans.llm_error is not None
    assert "couldn't reach" in ans.text


def test_no_docs_requires_qwen(capsys: pytest.CaptureFixture[str]) -> None:
    from doc_chat.__main__ import main

    assert main(["--text", "hi", "--no-docs"]) == 2
    assert "--no-docs and --stream need --llm qwen" in capsys.readouterr().err


def _fake_endpoint(seen: list[dict[str, Any]]) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps(
                {
                    "id": "x",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "m",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ready"},
                        }
                    ],
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
    return httpd


def test_secret_files_win_over_env_and_address_is_normalized(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from doc_chat.qwen import build_qwen_chat_model

    seen: list[dict[str, Any]] = []
    httpd = _fake_endpoint(seen)
    try:
        (tmp_path / "api_key.txt").write_text("file-key\n")
        (tmp_path / "host_address.txt").write_text(f"127.0.0.1:{httpd.server_address[1]}\n")
        monkeypatch.setenv("ONIT_SECRETS_DIR", str(tmp_path))
        monkeypatch.setenv("ONIT_API_KEY", "env-key")
        monkeypatch.setenv("ONIT_BASE_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("ONIT_MODEL", "fake/Qwen")
        llm = build_qwen_chat_model()
        llm.invoke("hi")
    finally:
        httpd.shutdown()

    assert seen == [{"path": "/v1/chat/completions", "auth": "Bearer file-key"}]


def test_env_is_the_fallback_when_files_are_missing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from doc_chat.qwen import build_qwen_chat_model

    seen: list[dict[str, Any]] = []
    httpd = _fake_endpoint(seen)
    try:
        monkeypatch.setenv("ONIT_SECRETS_DIR", str(tmp_path / "missing"))
        monkeypatch.setenv("ONIT_API_KEY", "env-key")
        monkeypatch.setenv("ONIT_BASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}/v1/")
        monkeypatch.setenv("ONIT_MODEL", "fake/Qwen")
        build_qwen_chat_model().invoke("hi")
    finally:
        httpd.shutdown()

    assert seen[0]["auth"] == "Bearer env-key"


def test_not_configured_and_failure_text_never_contain_values(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from doc_chat.qwen import build_qwen_chat_model, describe_failure, normalize_base_url

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONIT_SECRETS_DIR", str(tmp_path / "none"))
    for var in ("ONIT_API_KEY", "ONIT_BASE_URL", "ONIT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as exc:
        build_qwen_chat_model()
    assert exc.value.code == 2
    assert capsys.readouterr().err.strip() == "qwen backend not configured"

    class Rejected(Exception):
        status_code = 401

    msg = describe_failure(Rejected("Bearer sk-secret at http://10.0.0.1"))
    assert "sk-secret" not in msg and "10.0.0.1" not in msg and "401" in msg
    assert normalize_base_url(" 10.0.0.1:8000 ") == "http://10.0.0.1:8000/v1"
    assert normalize_base_url("https://h/v1/") == "https://h/v1"
