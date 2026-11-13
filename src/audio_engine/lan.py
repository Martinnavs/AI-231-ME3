"""The laptop's speech server (lan_speech) as an audio_engine backend: transcribe and synthesize."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import websockets.asyncio.client as ws_client

from .base import Synthesizer, Transcriber, TranscriptionStream
from .types import AudioClip, Transcript, Voice
from .wav import float_to_pcm16, pcm16_to_float, resample

FRAME_BYTES = 3200  # 100 ms of 16 kHz 16-bit mono
SETTLE_S = 0.4  # no message for this long after "finalize": the server has nothing more to say
MAX_REPLY_BYTES = 16 * 1024 * 1024


def _headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


class LanTranscriber(Transcriber):
    sample_rate = 16000

    def __init__(self, url: str, token: str | None = None) -> None:
        self._url = url.rstrip("/")
        self._headers = _headers(token)

    async def transcribe(self, clip: AudioClip) -> Transcript:
        pcm = float_to_pcm16(resample(clip, self.sample_rate).samples)
        texts: list[str] = []
        seen = 0
        async with ws_client.connect(f"{self._url}/stt", additional_headers=self._headers) as ws:

            async def read() -> None:
                nonlocal seen
                async for raw in ws:
                    msg = json.loads(raw)
                    seen += 1
                    if msg["type"] == "error":
                        raise RuntimeError(f"lan speech error {msg['status']}: {msg['reason']}")
                    if msg["type"] == "transcript" and msg["is_final"] and msg["text"].strip():
                        texts.append(msg["text"].strip())

            reader = asyncio.create_task(read())
            await ws.send(json.dumps({"type": "start", "sample_rate": self.sample_rate}))
            for i in range(0, len(pcm), FRAME_BYTES):
                await ws.send(pcm[i : i + FRAME_BYTES])
            await ws.send(json.dumps({"type": "finalize"}))
            while not reader.done():
                before = seen
                await asyncio.wait({reader}, timeout=SETTLE_S)
                if seen == before:
                    break
            if reader.done():
                reader.result()
            reader.cancel()
        return Transcript(" ".join(texts), None, True, None, True)

    def open_stream(self) -> TranscriptionStream:
        raise NotImplementedError("the lan backend does not stream")


class LanSynthesizer(Synthesizer):
    sample_rate = 24000

    def __init__(self, url: str, token: str | None = None) -> None:
        self._url = url.rstrip("/")
        self._headers = _headers(token)

    async def synthesize(self, text: str, voice: str | None = None) -> AudioClip:
        chunks: list[bytes] = []
        async with ws_client.connect(
            f"{self._url}/tts", additional_headers=self._headers, max_size=MAX_REPLY_BYTES
        ) as ws:
            self.sample_rate = json.loads(await ws.recv())["sample_rate"]
            await ws.send(json.dumps({"type": "speak", "context": 1, "text": text, "voice": voice}))
            async for raw in ws:
                if isinstance(raw, bytes):
                    chunks.append(raw[4:])
                    continue
                msg = json.loads(raw)
                if msg["type"] == "error":
                    raise RuntimeError(f"lan speech error {msg['status']}: {msg['reason']}")
                if msg["type"] == "done":
                    break
        return AudioClip(pcm16_to_float(b"".join(chunks)), self.sample_rate)

    def stream_synthesize(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        raise NotImplementedError("the lan backend does not stream")

    async def list_voices(self) -> list[Voice]:
        http_url = self._url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
        async with httpx.AsyncClient() as http:
            reply = await http.get(f"{http_url}/voices", headers=self._headers)
        reply.raise_for_status()
        return [Voice(v["id"], v["name"], v.get("language")) for v in reply.json()]
