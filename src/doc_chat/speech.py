"""Speech client for LAN speech server over WebSocket (ticket 24)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
import wave
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lan_speech.pause import NoiseFloor, PauseDetector
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


WAV_LEAD_IN_S = 0.5


@dataclass(frozen=True)
class SpokenTurn:
    pcm: bytes
    first_audio_ms: float
    total_ms: float
    speech_end_to_first_ms: float
    sentences: list[str]


@dataclass(frozen=True)
class HeardTurn:
    """What the speech server made of one spoken turn, and when.

    ``repair`` is the server's summary of the name-repair step (rung, question, matches) when the
    Parakeet engine is running; ``raw_text`` is the recogniser's words before repair.
    """

    text: str
    raw_text: str | None
    repair: dict[str, Any] | None
    t_speech_end: float  # perf_counter at the last sound of speech (mic) or the end of the audio
    t_final: float  # perf_counter when the final transcript arrived
    finalize_sent: float | None  # perf_counter when this client asked the server to end the turn
    pcm: bytes = b""  # the audio of this turn as sent to the server (16 kHz, 16-bit mono)

    @property
    def stt_ms(self) -> float:
        """From the last speech to the final transcript (the client's pause included)."""
        return (self.t_final - self.t_speech_end) * 1000.0

    @property
    def server_ms(self) -> float | None:
        """How long the server took to answer our `finalize` (None if it ended the turn itself)."""
        if self.finalize_sent is None:
            return None
        return (self.t_final - self.finalize_sent) * 1000.0


class NoSpeech(Exception):
    """listen_turn heard nothing within ``first_speech_timeout`` seconds."""


STITCH_GAP_S = 0.4  # silence put between two parts of a turn when their audio is joined

DEFAULT_NOISE_HINT = 0.015  # about -36 dBFS: a quiet room; replaced once the real room is measured


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
        self._noise = NoiseFloor()
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

    async def hear_wav(
        self,
        wav_path: Path | str,
        chunk_ms: int = 100,
        slot: str | None = None,
        risk: str | None = None,
    ) -> HeardTurn:
        """Stream a WAV file at real time, ask the server to end the turn, return the result."""
        if self._stt_ws is None:
            raise RuntimeError("STT socket not connected")

        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            pcm_data = wf.readframes(wf.getnframes())

        if channels != 1 or sampwidth != 2:
            import numpy as np

            arr = np.frombuffer(pcm_data, dtype=np.int16)
            if channels > 1:
                arr = arr.reshape(-1, channels).mean(axis=1).astype(np.int16)
            pcm_data = arr.tobytes()

        # Send start, then a short lead-in of silence: the streaming models drop a word that
        # begins right at sample 0, which a live microphone stream never does
        await self._stt_ws.send(StartMessage(sample_rate=rate, slot=slot, risk=risk).to_json())
        await self._stt_ws.send(b"\x00\x00" * int(rate * WAV_LEAD_IN_S))

        chunk_bytes = int(rate * (chunk_ms / 1000.0) * 2)
        for offset in range(0, len(pcm_data), chunk_bytes):
            await self._stt_ws.send(pcm_data[offset : offset + chunk_bytes])
            await asyncio.sleep(chunk_ms / 1000.0)

        t_speech_end = time.perf_counter()
        await self._stt_ws.send(FinalizeMessage().to_json())

        while True:
            msg_raw = await self._stt_ws.recv()
            if isinstance(msg_raw, bytes):
                continue
            parsed = parse_message(msg_raw)
            if isinstance(parsed, TranscriptMessage):
                if parsed.is_final or parsed.end_of_turn:
                    return HeardTurn(
                        parsed.text,
                        parsed.raw_text,
                        parsed.repair,
                        t_speech_end,
                        time.perf_counter(),
                        t_speech_end,
                    )
            elif isinstance(parsed, ErrorMessage):
                raise RuntimeError(f"STT error: {parsed.reason}")

    async def hear_pcm(
        self,
        pcm: bytes,
        *,
        slot: str | None = None,
        risk: str | None = None,
        block_ms: int = 100,
        t_speech_end: float | None = None,
    ) -> HeardTurn:
        """Send audio that is already recorded as one turn (not paced) and return the transcript.

        Used to decode two parts of a sentence as one: the speaker trailed off, was asked whether
        there was more, and went on; the parts' audio is joined and decoded once.
        """
        if self._stt_ws is None:
            raise RuntimeError("STT socket not connected")
        ws = self._stt_ws
        rate = 16000
        await ws.send(StartMessage(sample_rate=rate, slot=slot, risk=risk).to_json())
        step = int(rate * block_ms / 1000) * 2
        for offset in range(0, len(pcm), step):
            await ws.send(pcm[offset : offset + step])
        t_sent = time.perf_counter()
        await ws.send(FinalizeMessage().to_json())
        while True:
            raw = await ws.recv()
            if isinstance(raw, bytes):
                continue
            parsed = parse_message(raw)
            if isinstance(parsed, TranscriptMessage) and (parsed.is_final or parsed.end_of_turn):
                return HeardTurn(
                    parsed.text.strip(),
                    parsed.raw_text,
                    parsed.repair,
                    t_speech_end if t_speech_end is not None else t_sent,
                    time.perf_counter(),
                    t_sent,
                    pcm,
                )
            if isinstance(parsed, ErrorMessage):
                raise RuntimeError(f"STT error: {parsed.reason}")

    async def send_stt_wav(
        self,
        wav_path: Path | str,
        chunk_ms: int = 100,
    ) -> tuple[str, float, float]:
        """Stream a WAV file; returns (transcript, stt_final_ms, t_speech_end)."""
        heard = await self.hear_wav(wav_path, chunk_ms)
        return heard.text, heard.stt_ms, heard.t_speech_end

    async def _mic_chunks(self, rate: int, block_ms: int) -> AsyncIterator[bytes]:
        """The default microphone as 100 ms blocks of 16-bit samples; closes it when dropped."""
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def on_audio(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with sd.RawInputStream(
            samplerate=rate,
            channels=1,
            dtype="int16",
            blocksize=rate * block_ms // 1000,
            callback=on_audio,
        ):
            while True:
                yield await queue.get()

    async def listen_turn(
        self,
        *,
        slot: str | None = None,
        risk: str | None = None,
        vad_hold: float = 0.6,
        on_partial: Callable[[str], None] | None = None,
        block_ms: int = 100,
        audio: AsyncIterator[bytes] | None = None,
        first_speech_timeout: float | None = None,
    ) -> HeardTurn:
        """Listen until one non-empty turn is transcribed.

        With ``first_speech_timeout`` the wait is bounded: if nothing is said within that many
        seconds, NoSpeech is raised (the user was asked something and did not answer).

        The turn ends when this client has heard ``vad_hold`` seconds of quiet after speech and
        sent `finalize` (what the Pi's VAD does), or when the server ends it first. With
        ``vad_hold`` 0 only the server decides, which on Kroko takes 1.5 to 2.5 s. ``audio`` is a
        source of 16 kHz 16-bit blocks; the microphone when omitted.
        """
        if self._stt_ws is None:
            raise RuntimeError("STT socket not connected")
        import numpy as np

        rate = 16000
        ws = self._stt_ws
        hint = self._noise.value if self._noise.value is not None else DEFAULT_NOISE_HINT
        detector = PauseDetector(vad_hold, noise=hint)
        state: dict[str, float | None] = {"last_voiced": None, "finalize_sent": None}
        sent = bytearray()  # every block sent, so the turn's audio can be joined with a follow-up
        cut: list[int] = []  # length of `sent` when we asked for the end of the turn
        t_start = time.perf_counter()
        source = audio if audio is not None else self._mic_chunks(rate, block_ms)

        async def pump() -> None:
            async for chunk in source:
                x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                level = float(np.sqrt(np.mean(x**2))) if x.size else 0.0
                voiced, pause = detector.feed(level, len(chunk) / 2 / rate)
                if voiced:
                    state["last_voiced"] = time.perf_counter()
                await ws.send(chunk)
                sent.extend(chunk)
                if pause and vad_hold > 0:
                    state["finalize_sent"] = time.perf_counter()
                    cut[:] = [len(sent)]
                    await ws.send(FinalizeMessage().to_json())

        await ws.send(StartMessage(sample_rate=rate, slot=slot, risk=risk).to_json())
        pump_task = asyncio.create_task(pump())
        try:
            while True:
                if first_speech_timeout is None:
                    raw = await ws.recv()
                else:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.2)
                    except TimeoutError:
                        if (
                            state["last_voiced"] is None
                            and time.perf_counter() - t_start > first_speech_timeout
                        ):
                            raise NoSpeech from None
                        continue
                if isinstance(raw, bytes):
                    continue
                parsed = parse_message(raw)
                if isinstance(parsed, TranscriptMessage):
                    text = parsed.text.strip()
                    if parsed.end_of_turn and text:
                        now = time.perf_counter()
                        # a final that answers our `finalize` owns the audio up to that moment;
                        # what was recorded while the server worked is the next turn's start
                        own = (
                            bytes(sent[: cut[0]]) if cut and state["finalize_sent"] else bytes(sent)
                        )
                        return HeardTurn(
                            text,
                            parsed.raw_text,
                            parsed.repair,
                            state["last_voiced"] or now,
                            now,
                            state["finalize_sent"],
                            own,
                        )
                    if parsed.end_of_turn:
                        state["finalize_sent"] = None  # nothing was said: keep listening
                    elif on_partial is not None and text:
                        on_partial(text)
                elif isinstance(parsed, ErrorMessage):
                    raise RuntimeError(f"STT error: {parsed.reason}")
        finally:
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
            if detector.measured:
                self._noise.value = detector.noise
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    async def listen_mic(
        self,
        on_partial: Callable[[str], None] | None = None,
        block_ms: int = 100,
    ) -> tuple[str, float]:
        """Listen with only the server deciding when the turn ends; returns (text, t_final)."""
        heard = await self.listen_turn(vad_hold=0, on_partial=on_partial, block_ms=block_ms)
        return heard.text, heard.t_final

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

    async def speak_sentences(
        self,
        sentences: Iterable[str],
        voice: str | None = "F1",
        t_speech_end: float | None = None,
        on_chunk: Callable[[bytes], None] | None = None,
        on_sentence: Callable[[str], None] | None = None,
    ) -> SpokenTurn:
        """Speak sentences one after another as the iterable produces them.

        The iterable may block (it is a live LLM stream), so it is advanced in a worker thread.
        Each sentence is sent as soon as the previous one has finished synthesizing, which is much
        faster than playback, so a player fed through `on_chunk` hears no gap.
        """
        iterator = iter(sentences)
        done = object()

        def advance() -> object:
            return next(iterator, done)

        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        parts: list[bytes] = []
        spoken: list[str] = []
        first_audio_ms = 0.0
        speech_end_to_first = 0.0
        while (sentence := await loop.run_in_executor(None, advance)) is not done:
            assert isinstance(sentence, str)
            if on_sentence is not None:
                on_sentence(sentence)
            pcm, first_ms, _total, to_first = await self.speak_text(
                sentence,
                voice=voice,
                t_speech_end=t_speech_end if not spoken else None,
                on_chunk=on_chunk,
            )
            if not spoken:
                first_audio_ms, speech_end_to_first = first_ms, to_first
            parts.append(pcm)
            spoken.append(sentence)
        total_ms = (time.perf_counter() - t0) * 1000.0
        return SpokenTurn(b"".join(parts), first_audio_ms, total_ms, speech_end_to_first, spoken)

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
