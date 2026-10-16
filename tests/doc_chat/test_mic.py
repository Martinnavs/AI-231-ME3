"""--mic loop against the real fake-engine server, with a fake sounddevice."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
import types
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("websockets")
pytest.importorskip("langchain_core")
pytest.importorskip("langchain_community")

from doc_chat.__main__ import main as doc_chat_main  # noqa: E402
from lan_speech.engines import (  # noqa: E402
    FakeSTTEngine,
    FakeSTTStream,
    FakeTTSEngine,
    STTDecodeResult,
    STTStream,
)
from lan_speech.server import LanSpeechServer  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def mic_server() -> Generator[int, None, None]:
    port = _free_port()
    streams: list[STTStream] = [
        FakeSTTStream(),
        FakeSTTStream(responses=[STTDecodeResult("quit", True, True)]),
    ]

    def factory() -> STTStream:
        return streams.pop(0) if streams else FakeSTTStream(finalize_text="")

    server = LanSpeechServer(
        stt_engine=FakeSTTEngine(stream_factory=factory),
        tts_engine=FakeTTSEngine(delay_s=0.01),
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

    th = threading.Thread(target=run, daemon=True)
    th.start()
    ready.wait(timeout=5.0)
    try:
        yield port
    finally:
        asyncio.run_coroutine_threadsafe(server.stop(), loop).result(timeout=5.0)
        loop.call_soon_threadsafe(loop.stop)
        th.join(timeout=5.0)


def _fake_sounddevice(played: list[bytes]) -> types.ModuleType:
    mod = types.ModuleType("sounddevice")

    class RawInputStream:
        def __init__(self, **kw: Any) -> None:
            self._cb = kw["callback"]
            self._block = kw["blocksize"] * 2
            self._stop = threading.Event()
            self._th = threading.Thread(target=self._feed, daemon=True)

        def _feed(self) -> None:
            while not self._stop.is_set():
                self._cb(b"\x00" * self._block, self._block // 2, None, None)
                time.sleep(0.01)

        def __enter__(self) -> RawInputStream:
            self._th.start()
            return self

        def __exit__(self, *exc: object) -> None:
            self._stop.set()
            self._th.join(timeout=2.0)

    class RawOutputStream:
        def __init__(self, **kw: Any) -> None:
            pass

        def start(self) -> None: ...

        def stop(self) -> None: ...

        def close(self) -> None: ...

        def write(self, data: bytes) -> None:
            played.append(data)

    mod.RawInputStream = RawInputStream  # type: ignore[attr-defined]
    mod.RawOutputStream = RawOutputStream  # type: ignore[attr-defined]
    return mod


def test_mic_turn_then_voice_quit(
    mic_server: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    played: list[bytes] = []
    monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(played))
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "lights.md").write_text("You can turn on the lights with a voice command.")
    log = tmp_path / "turns.jsonl"

    rc = doc_chat_main(
        [
            "--server",
            f"ws://127.0.0.1:{mic_server}",
            "--docs",
            str(docs),
            "--mic",
            "--play",
            "--log",
            str(log),
        ]
    )

    assert rc == 0
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["question"] == "turn on the lights"
    assert rows[0]["source"] == "lights.md"
    assert rows[0]["speech_end_to_first_audio_ms"] >= 0
    assert played, "answer audio was written to the output stream"
