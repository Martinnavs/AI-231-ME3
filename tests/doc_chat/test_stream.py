"""Sentence streaming, background playback and prompt rendering."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import types
from collections.abc import Generator, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("websockets")
pytest.importorskip("langchain_core")
pytest.importorskip("langchain_openai")

from doc_chat.__main__ import main as doc_chat_main  # noqa: E402
from doc_chat.chain import (  # noqa: E402
    SentenceStream,
    build_sentence_streamer,
    render_system,
)
from lan_speech.engines import FakeSTTEngine, FakeTTSEngine  # noqa: E402
from lan_speech.server import LanSpeechServer  # noqa: E402


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class ScriptedLLM:
    """Stands in for a chat model: streams the given pieces, optionally failing afterwards."""

    def __init__(self, pieces: list[str], fail_after: bool = False) -> None:
        self.pieces = pieces
        self.fail_after = fail_after
        self.seen: list[Any] = []

    def stream(self, messages: Any) -> Iterator[_Chunk]:
        self.seen.append(messages)
        for piece in self.pieces:
            yield _Chunk(piece)
        if self.fail_after:
            raise TimeoutError("cut off")


def _stream(llm: ScriptedLLM, max_words: int = 40, retriever: Any = None) -> SentenceStream:
    return build_sentence_streamer(retriever, llm, max_words=max_words)("What is it?")  # type: ignore[arg-type]


def test_sentences_are_released_as_the_next_one_starts() -> None:
    llm = ScriptedLLM(["Paris is ", "the capital", ". It is ", "big.", " Bye now."])
    stream = _stream(llm)
    it = iter(stream)
    assert next(it) == "Paris is the capital."
    assert stream.first_token_ms is not None and stream.llm_ms is None
    assert list(it) == ["It is big.", "Bye now."]
    assert stream.text == "Paris is the capital. It is big. Bye now."
    assert stream.llm_ms is not None and stream.error is None


def test_initials_and_decimals_do_not_split_mid_stream() -> None:
    llm = ScriptedLLM(["J.K. Rowling", " wrote it. Pi is 3.", "14 roughly", ". Done."])
    assert list(_stream(llm)) == ["J.K. Rowling wrote it.", "Pi is 3.14 roughly.", "Done."]


def test_thinking_blocks_are_never_spoken() -> None:
    llm = ScriptedLLM(["<thi", "nk>planning. more.</think>", "Hello there. ", "Bye."])
    assert list(_stream(llm)) == ["Hello there.", "Bye."]
    unclosed = ScriptedLLM(["Hi there. ", "<think>still reasoning. no"])
    assert list(_stream(unclosed)) == ["Hi there."]


def test_markdown_is_stripped_and_word_cap_stops_at_a_sentence() -> None:
    llm = ScriptedLLM(["**Big** news today. ", "Second one is here. ", "Third is long enough."])
    stream = _stream(llm, max_words=7)
    assert list(stream) == ["Big news today.", "Second one is here."]
    assert stream.text == "Big news today. Second one is here."


def test_first_sentence_longer_than_cap_is_cut_at_a_word() -> None:
    llm = ScriptedLLM(["one two three four five six seven eight. Next."])
    assert list(_stream(llm, max_words=4)) == ["one two three four"]


def test_failure_before_any_sentence_falls_back_and_reports() -> None:
    class Down(ScriptedLLM):
        def stream(self, messages: Any) -> Iterator[_Chunk]:
            raise ConnectionError("no route")
            yield  # pragma: no cover

    stream = _stream(Down([]))
    assert list(stream) == ["Sorry, I couldn't reach the language model."]
    assert stream.error is not None and stream.error.startswith("ConnectionError")


def test_failure_after_some_sentences_keeps_what_was_said() -> None:
    llm = ScriptedLLM(["First one. ", "Second one. ", "Third is cut"], fail_after=True)
    stream = _stream(llm)
    assert list(stream) == ["First one.", "Second one."]
    assert stream.error is not None and stream.error.startswith("TimeoutError")


def test_empty_reply_gets_a_neutral_apology() -> None:
    assert list(_stream(ScriptedLLM(["   "]))) == ["Sorry, I don't have an answer for that."]


def test_retrieved_passages_become_the_system_context() -> None:
    from langchain_core.documents import Document

    class R:
        def invoke(self, q: str) -> list[Document]:
            return [
                Document(page_content="# Harry\n\nHarry is a wizard.", metadata={"source": "h.md"})
            ]

    llm = ScriptedLLM(["Yes. "])
    stream = _stream(llm, retriever=R())
    assert list(stream) == ["Yes."]
    assert stream.source == "h.md"
    system = llm.seen[0][0].content
    assert "Harry is a wizard." in system and "#" not in system


def test_render_system_is_brace_safe_and_appends_missing_context() -> None:
    assert render_system("Be {brief}. Use: {context}", "CTX") == "Be {brief}. Use: CTX"
    assert render_system("Be brief.", "CTX") == "Be brief.\n\nContext:\nCTX"
    assert "{context}" not in render_system(None, None)
    assert render_system("Chat. {context}", None) == "Chat."


def test_player_plays_in_the_background_and_close_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[bytes] = []
    started = threading.Event()
    release = threading.Event()

    class Out:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

        def start(self) -> None:
            started.set()

        def write(self, data: bytes) -> None:
            release.wait(2.0)
            written.append(data)

        def stop(self) -> None: ...

        def close(self) -> None: ...

    mod = types.ModuleType("sounddevice")
    mod.RawOutputStream = Out  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    from doc_chat.playback import Player

    player = Player(44100)
    assert started.is_set()
    player.push(b"a")
    player.push(b"b")
    assert written == []  # push returned although the speaker is still busy
    release.set()
    player.close()
    assert written == [b"a", b"b"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def fake_speech_server() -> Generator[tuple[int, FakeTTSEngine], None, None]:
    port = _free_port()
    tts = FakeTTSEngine(delay_s=0.0)
    server = LanSpeechServer(
        stt_engine=FakeSTTEngine(),
        tts_engine=tts,
        host="127.0.0.1",
        port=port,
        insecure_loopback=True,
    )
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(5.0)
    try:
        yield port, tts
    finally:
        asyncio.run_coroutine_threadsafe(server.stop(), loop).result(5.0)
        loop.call_soon_threadsafe(loop.stop)


def _sse_endpoint(requests: list[dict[str, Any]]) -> HTTPServer:
    pieces = ["Paris is the capital", ". It is also ", "very large", ". Enjoy."]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in [*pieces, None]:
                delta = {"content": piece} if piece is not None else {}
                body = {
                    "id": "x",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "m",
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": None if piece is not None else "stop",
                        }
                    ],
                }
                self.wfile.write(f"data: {json.dumps(body)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args: object) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_stream_flag_end_to_end_with_streaming_endpoint(
    fake_speech_server: tuple[int, FakeTTSEngine],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    port, tts = fake_speech_server
    requests: list[dict[str, Any]] = []
    httpd = _sse_endpoint(requests)
    try:
        secrets = tmp_path / "secrets"
        secrets.mkdir()
        (secrets / "api_key.txt").write_text("k\n")
        (secrets / "host_address.txt").write_text(f"127.0.0.1:{httpd.server_address[1]}\n")
        monkeypatch.setenv("ONIT_SECRETS_DIR", str(secrets))
        monkeypatch.setenv("ONIT_MODEL", "fake/Qwen")
        log = tmp_path / "turns.jsonl"
        rc = doc_chat_main(
            [
                "--server",
                f"ws://127.0.0.1:{port}",
                "--text",
                "Tell me about Paris",
                "--llm",
                "qwen",
                "--no-docs",
                "--stream",
                "--llm-max-tokens",
                "50",
                "--log",
                str(log),
            ]
        )
    finally:
        httpd.shutdown()

    assert rc == 0
    assert requests[0]["stream"] is True
    assert requests[0]["max_completion_tokens"] == 50
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert tts.synth_calls == [
        "Paris is the capital.",
        "It is also very large.",
        "Enjoy.",
    ]
    rec = json.loads(log.read_text().splitlines()[0])
    assert rec["answer_words"] == 10
    assert rec["llm_first_token_ms"] is not None and rec["llm_ms"] >= rec["llm_first_token_ms"]
    assert rec["llm_error"] is None
    out = capsys.readouterr().out
    assert "bot:  Paris is the capital." in out and "bot:  Enjoy." in out
    assert "answer starts" in out


def test_a_flushed_sentence_is_released_without_waiting_for_the_next() -> None:
    from doc_chat.chain import FLUSH

    llm = ScriptedLLM(["Checking the weather in Baguio. " + FLUSH])
    it = iter(_stream(llm))
    assert next(it) == "Checking the weather in Baguio."  # same chunk, nothing after it yet
    llm = ScriptedLLM(["Checking it. " + FLUSH, "It is 20. ", "Sunny."])
    assert list(_stream(llm)) == ["Checking it.", "It is 20.", "Sunny."]
