"""Speech client for LAN speech server over WebSocket (ticket 24)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lan_speech.protocol import (
    CancelMessage,
    DoneMessage,
    ErrorMessage,
    FinalizeMessage,
    ReadyMessage,
    SpeakMessage,
    StartMessage,
    TranscriptMessage,
    parse_message,
    unpack_tts_audio,
)

try:
    import websockets.asyncio.client as ws_client
    import websockets.exceptions as wexc
except ImportError:
    ws_client = None  # type: ignore[assignment]
    wexc = None  # type: ignore[assignment]


def _format_ws_url(base_url: str, endpoint: str) -> str:
    url = base_url.strip()
    if url.startswith("http://"):
        url = "ws://" + url[7:]
    elif url.startswith("https://"):
        url = "wss://" + url[8:]
    elif not url.startswith("ws://") and not url.startswith("wss://"):
        url = "ws://" + url
    return url.rstrip("/") + "/" + endpoint.lstrip("/")


async def connect_ws(url: str, token: str | None = None) -> Any:
    """Connect to a WebSocket endpoint with Bearer auth and uniform error exits."""
    if ws_client is None:
        print("install the laptop group for speech", file=sys.stderr)
        sys.exit(2)

    headers: list[tuple[str, str]] = []
    if token:
        headers.append(("Authorization", f"Bearer {token}"))

    try:
        return await ws_client.connect(
            url,
            additional_headers=headers,
            reconnect_delays=None,  # type: ignore[arg-type]
        )
    except wexc.InvalidStatus as exc:
        status = exc.response.status_code
        if status == 401:
            print("speech server rejected token", file=sys.stderr)
            sys.exit(2)
        print("speech server unavailable", file=sys.stderr)
        sys.exit(2)
    except (OSError, wexc.WebSocketException):
        print("speech server unavailable", file=sys.stderr)
        sys.exit(2)


class DocChatSpeechClient:
    """Manages STT and TTS WebSocket sessions."""

    def __init__(self, server_url: str = "ws://127.0.0.1:8765", token: str | None = None) -> None:
        self.server_url = server_url
        self.token = token if token is not None else os.environ.get("LAN_SPEECH_TOKEN")
        self.stt_url = _format_ws_url(server_url, "stt")
        self.tts_url = _format_ws_url(server_url, "tts")
        self._stt_ws: Any = None
        self._tts_ws: Any = None
        self.tts_sample_rate: int = 24000
        self._current_context: int = 0
        self._active_speak_task: asyncio.Task[Any] | None = None

    async def connect(self) -> None:
        self._stt_ws = await connect_ws(self.stt_url, self.token)
        self._tts_ws = await connect_ws(self.tts_url, self.token)
        # TTS server sends ready message on connect
        ready_raw = await self._tts_ws.recv()
        ready_msg = parse_message(ready_raw)
        if isinstance(ready_msg, ReadyMessage):
            self.tts_sample_rate = ready_msg.sample_rate

    async def close(self) -> None:
        if self._stt_ws is not None:
            await self._stt_ws.close()
            self._stt_ws = None
        if self._tts_ws is not None:
            await self._tts_ws.close()
            self._tts_ws = None

    async def send_stt_wav(
        self,
        wav_path: Path | str,
        chunk_ms: int = 100,
    ) -> tuple[str, float, float]:
        """Stream a 16kHz WAV file paced at real time.

        Returns (transcript, stt_final_ms, t_speech_end).
        """
        if self._stt_ws is None:
            raise RuntimeError("STT socket not connected")

        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()
            pcm_data = wf.readframes(n_frames)

        if channels != 1 or sampwidth != 2:
            import numpy as np

            arr = np.frombuffer(pcm_data, dtype=np.int16)
            if channels > 1:
                arr = arr.reshape(-1, channels).mean(axis=1).astype(np.int16)
            pcm_data = arr.tobytes()

        # Send start
        await self._stt_ws.send(StartMessage(sample_rate=rate).to_json())

        chunk_bytes = int(rate * (chunk_ms / 1000.0) * 2)
        total_len = len(pcm_data)
        offset = 0

        while offset < total_len:
            end = min(offset + chunk_bytes, total_len)
            chunk = pcm_data[offset:end]
            offset = end
            await self._stt_ws.send(chunk)
            await asyncio.sleep(chunk_ms / 1000.0)

        t_speech_end = time.perf_counter()
        await self._stt_ws.send(FinalizeMessage().to_json())

        # Wait for final transcript
        transcript_text = ""
        while True:
            msg_raw = await self._stt_ws.recv()
            if isinstance(msg_raw, bytes):
                continue
            parsed = parse_message(msg_raw)
            if isinstance(parsed, TranscriptMessage):
                if parsed.is_final or parsed.end_of_turn:
                    transcript_text = parsed.text
                    break
            elif isinstance(parsed, ErrorMessage):
                raise RuntimeError(f"STT error: {parsed.reason}")

        t_final = time.perf_counter()
        stt_final_ms = (t_final - t_speech_end) * 1000.0
        return transcript_text, stt_final_ms, t_speech_end

    async def listen_mic(
        self,
        on_partial: Callable[[str], None] | None = None,
        block_ms: int = 100,
    ) -> tuple[str, float]:
        """Stream the default microphone until the server ends a non-empty turn.

        Returns (transcript, t_final) where t_final is when the end-of-turn transcript arrived.
        """
        if self._stt_ws is None:
            raise RuntimeError("STT socket not connected")
        import sounddevice as sd

        rate = 16000
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def on_audio(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        async def pump() -> None:
            while True:
                await self._stt_ws.send(await queue.get())

        await self._stt_ws.send(StartMessage(sample_rate=rate).to_json())
        with sd.RawInputStream(
            samplerate=rate,
            channels=1,
            dtype="int16",
            blocksize=rate * block_ms // 1000,
            callback=on_audio,
        ):
            pump_task = asyncio.create_task(pump())
            try:
                while True:
                    raw = await self._stt_ws.recv()
                    if isinstance(raw, bytes):
                        continue
                    parsed = parse_message(raw)
                    if isinstance(parsed, TranscriptMessage):
                        if parsed.end_of_turn and parsed.text.strip():
                            return parsed.text.strip(), time.perf_counter()
                        if on_partial is not None and parsed.text.strip():
                            on_partial(parsed.text.strip())
                    elif isinstance(parsed, ErrorMessage):
                        raise RuntimeError(f"STT error: {parsed.reason}")
            finally:
                pump_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump_task

    async def speak_text(
        self,
        text: str,
        voice: str | None = "F1",
        t_speech_end: float | None = None,
        on_chunk: Callable[[bytes], None] | None = None,
    ) -> tuple[bytes, float, float, float]:
        """Send speak to TTS, collect audio.

        Returns (pcm_bytes, first_audio_ms, total_ms, speech_end_to_first_audio_ms).
        """
        if self._tts_ws is None:
            raise RuntimeError("TTS socket not connected")

        self._active_speak_task = asyncio.current_task()
        try:
            self._current_context += 1
            ctx = self._current_context

            await self._tts_ws.send(SpeakMessage(context=ctx, text=text, voice=voice).to_json())
            t_speak_start = time.perf_counter()

            first_audio_ms: float | None = None
            speech_end_to_first: float | None = None
            chunks: list[bytes] = []

            while True:
                msg = await self._tts_ws.recv()
                if isinstance(msg, bytes):
                    chunk_ctx, pcm = unpack_tts_audio(msg)
                    if chunk_ctx != ctx:
                        continue
                    if first_audio_ms is None:
                        t_first = time.perf_counter()
                        first_audio_ms = (t_first - t_speak_start) * 1000.0
                        if t_speech_end is not None:
                            speech_end_to_first = (t_first - t_speech_end) * 1000.0
                        else:
                            speech_end_to_first = first_audio_ms

                    chunks.append(pcm)
                    if on_chunk is not None:
                        on_chunk(pcm)
                else:
                    parsed = parse_message(msg)
                    if isinstance(parsed, DoneMessage):
                        if parsed.context == ctx:
                            break
                    elif isinstance(parsed, ErrorMessage):
                        raise RuntimeError(f"TTS error: {parsed.reason}")

            t_done = time.perf_counter()
            total_ms = (t_done - t_speak_start) * 1000.0
            pcm_all = b"".join(chunks)

            if first_audio_ms is None:
                first_audio_ms = total_ms
            if speech_end_to_first is None:
                speech_end_to_first = first_audio_ms

            return pcm_all, first_audio_ms, total_ms, speech_end_to_first
        finally:
            if self._active_speak_task is asyncio.current_task():
                self._active_speak_task = None

    async def cancel_current(self) -> float:
        """Send cancel for current context and return cancel_to_silence_ms."""
        if self._tts_ws is None or self._current_context == 0:
            return 0.0
        t_cancel = time.perf_counter()
        await self._tts_ws.send(CancelMessage(context=self._current_context).to_json())
        if self._active_speak_task is not None and not self._active_speak_task.done():
            self._active_speak_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._active_speak_task
            self._active_speak_task = None
        t_silence = time.perf_counter()
        return (t_silence - t_cancel) * 1000.0
