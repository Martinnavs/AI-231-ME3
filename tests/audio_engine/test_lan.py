from __future__ import annotations

import json
import struct
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent
from websockets.asyncio.server import serve

from audio_engine import AudioClip, create_synthesizer, create_transcriber
from audio_engine.lan import LanSynthesizer, LanTranscriber
from audio_engine.wav import float_to_pcm16, read_wav, write_wav

SRC = Path(__file__).resolve().parents[2] / "src"


class FakeSpeechServer:
    """The lan_speech wire protocol, with what it saw recorded and what it says scripted."""

    def __init__(
        self, finals: tuple[str, ...] = ("hello world",), error: str | None = None
    ) -> None:
        self.finals, self.error, self.rate = finals, error, 24000
        self.auth: list[str] = []
        self.started: list[dict[str, Any]] = []
        self.audio_bytes = 0
        self.spoken: list[dict[str, Any]] = []

    async def handle(self, ws: Any) -> None:
        self.auth.append(ws.request.headers.get("Authorization", ""))
        if ws.request.path == "/stt":
            await self.stt(ws)
        else:
            await self.tts(ws)

    async def stt(self, ws: Any) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                self.audio_bytes += len(raw)
                continue
            msg = json.loads(raw)
            if msg["type"] == "start":
                self.started.append(msg)
            elif self.error:
                await ws.send(json.dumps({"type": "error", "status": 500, "reason": self.error}))
            else:
                for text in self.finals:
                    final = {"type": "transcript", "text": text, "is_final": True}
                    await ws.send(json.dumps({**final, "end_of_turn": True, "t_audio_end_s": 1.0}))

    async def tts(self, ws: Any) -> None:
        await ws.send(json.dumps({"type": "ready", "sample_rate": self.rate}))
        async for raw in ws:
            msg = json.loads(raw)
            self.spoken.append(msg)
            if self.error:
                await ws.send(json.dumps({"type": "error", "status": 500, "reason": self.error}))
                continue
            frame = struct.pack(">I", msg["context"]) + float_to_pcm16(np.full(2400, 0.25, "f4"))
            await ws.send(frame)
            await ws.send(frame)
            await ws.send(json.dumps({"type": "done", "context": msg["context"]}))

    def process_request(self, connection: Any, request: Any) -> Any:
        if request.path == "/voices":
            body = json.dumps([{"id": "F1", "name": "Female 1", "language": "en"}]) + "\n"
            return connection.respond(HTTPStatus.OK, body)
        return None


@asynccontextmanager
async def serving(fake: FakeSpeechServer) -> AsyncIterator[str]:
    async with serve(fake.handle, "127.0.0.1", 0, process_request=fake.process_request) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


def one_second(rate: int = 48000) -> AudioClip:
    t = np.arange(rate) / rate
    return AudioClip((0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), rate)


async def test_transcribe_sends_16k_pcm_with_the_token_and_returns_the_final_text() -> None:
    fake = FakeSpeechServer()
    async with serving(fake) as url:
        result = await LanTranscriber(url, "tok").transcribe(one_second())
    assert result.text == "hello world" and result.is_final
    assert fake.started == [{"type": "start", "sample_rate": 16000}]
    assert fake.audio_bytes == 32000 and fake.auth == ["Bearer tok"]


async def test_transcribe_joins_every_final_the_server_sends() -> None:
    async with serving(FakeSpeechServer(finals=("one two", "three"))) as url:
        result = await LanTranscriber(url).transcribe(one_second(16000))
    assert result.text == "one two three"


async def test_transcribe_raises_the_servers_error() -> None:
    async with serving(FakeSpeechServer(error="engine error")) as url:
        with pytest.raises(RuntimeError, match="lan speech error 500: engine error"):
            await LanTranscriber(url).transcribe(one_second(16000))


async def test_synthesize_returns_the_audio_at_the_servers_rate() -> None:
    fake = FakeSpeechServer()
    fake.rate = 22050
    async with serving(fake) as url:
        clip = await LanSynthesizer(url, "tok").synthesize("hi there", "F1")
    assert clip.sample_rate == 22050 and len(clip.samples) == 4800
    assert float(clip.samples.max()) == pytest.approx(0.25, abs=0.001)
    assert fake.spoken[0]["text"] == "hi there" and fake.spoken[0]["voice"] == "F1"
    assert fake.auth == ["Bearer tok"]


async def test_synthesize_raises_the_servers_error() -> None:
    async with serving(FakeSpeechServer(error="engine error")) as url:
        with pytest.raises(RuntimeError, match="lan speech error 500: engine error"):
            await LanSynthesizer(url).synthesize("hi")


async def test_list_voices_reads_the_http_endpoint() -> None:
    async with serving(FakeSpeechServer()) as url:
        voices = await LanSynthesizer(url).list_voices()
    assert [(v.id, v.name, v.language) for v in voices] == [("F1", "Female 1", "en")]


def test_the_streaming_methods_are_not_supported() -> None:
    with pytest.raises(NotImplementedError):
        LanTranscriber("ws://x").open_stream()
    with pytest.raises(NotImplementedError):
        LanSynthesizer("ws://x").stream_synthesize("hi")


def test_lan_is_registered_and_created_by_name() -> None:
    assert isinstance(create_transcriber("lan", url="ws://x:1"), LanTranscriber)
    assert isinstance(create_synthesizer("lan", url="ws://x:1", token="t"), LanSynthesizer)


async def test_the_speech_mcp_server_uses_the_lan_backend(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    write_wav(media / "in.wav", one_second(16000))
    token = tmp_path / "token"
    token.write_text("tok\n")
    fake = FakeSpeechServer()
    async with serving(fake) as url:
        args = [
            "-m",
            "voice_agent.mcp_servers.speech",
            "--stt-backend",
            "lan",
            "--tts-backend",
            "lan",
        ]
        args += ["--lan-url", url, "--lan-token-file", str(token), "--media-root", str(media)]
        params = StdioServerParameters(
            command=sys.executable, args=args, env={"PYTHONPATH": str(SRC)}
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            heard = await session.call_tool("transcribe_audio_file", {"path": "in.wav"})
            said = await session.call_tool(
                "synthesize_speech_to_file", {"text": "hi", "path": "out.wav"}
            )
    first = heard.content[0]
    assert isinstance(first, TextContent) and json.loads(first.text)["text"] == "hello world"
    assert not said.isError and read_wav(media / "out.wav").sample_rate == 24000
    assert fake.auth == ["Bearer tok", "Bearer tok"]
