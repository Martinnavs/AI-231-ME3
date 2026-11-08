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

from doc_chat.fdbench_marks import mark
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
            max_size=16
            * 1024
            * 1024,  # one comma-free clause of TTS audio can exceed the 1 MiB default
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
    partial: str = ""  # the last interim text the streaming model showed before the final
    tail: bytes = b""  # audio heard after `finalize` and held back (the user may still be talking)

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


def room_noise_hint() -> float:
    """The noise level to start from: the calibrated room if out/calibration.json exists, else
    `DEFAULT_NOISE_HINT`."""
    from doc_chat.calibrate import load_calibration

    cal = load_calibration()
    return DEFAULT_NOISE_HINT if cal is None else float(10 ** (cal["noise_db"] / 20.0))


class MicHub:
    """One microphone stream kept open for the whole session.

    The assistant needs to hear the user while it thinks and while it talks, so the stream cannot
    be opened and closed per turn. Whoever is listening takes blocks with `get`; what nobody takes
    piles up and is thrown away with `drain` (it is the assistant's own voice, mostly).
    """

    def __init__(
        self,
        rate: int = 16000,
        block_ms: int = 100,
        canceller: Any = None,
        device: int | str | None = None,
    ) -> None:
        self.rate = rate
        self.block_ms = block_ms
        self.device = device  # sounddevice index or name part; None = the system default input
        self._canceller = canceller  # an EchoCanceller: every block goes through it, in order
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._stream: Any = None
        self.tap: Callable[[bytes], None] | None = None  # sees every post-AEC block, in order

    def start(self) -> None:
        import sounddevice as sd

        loop = asyncio.get_running_loop()

        def on_audio(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            loop.call_soon_threadsafe(self._take, bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=self.rate,
            channels=1,
            dtype="int16",
            blocksize=self.rate * self.block_ms // 1000,
            callback=on_audio,
            device=self.device,
        )
        self._stream.start()

    def _take(self, block: bytes) -> None:
        block = self._canceller.process(block) if self._canceller else block
        self._queue.put_nowait(block)
        if self.tap is not None:
            try:
                self.tap(block)
            except Exception as exc:  # a listener must never break the microphone
                print(f"  [mic tap failed: {type(exc).__name__}: {exc}]", flush=True)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def drain(self) -> int:
        dropped = 0
        while not self._queue.empty():
            self._queue.get_nowait()
            dropped += 1
        return dropped

    async def get(self) -> bytes:
        return await self._queue.get()

    async def blocks(self) -> AsyncIterator[bytes]:
        while True:
            yield await self.get()


async def with_lead_in(lead: Iterable[bytes], rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Some blocks that were already heard, then the live ones."""
    for block in lead:
        yield block
    async for block in rest:
        yield block


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
        self.hub: MicHub | None = None  # set to listen to the microphone across turns
        self.mic_device: int | str | None = None  # input for the per-turn stream (no hub)
        self.canceller: Any = None  # an EchoCanceller, handed to every remote player
        self._active_speak_task: asyncio.Task[Any] | None = None
        self.last_voiced: float = 0.0  # perf_counter of the last voiced block of the current listen
        self.audio_cache: dict[tuple[str, str | None], bytes] = {}  # (text, voice) -> 16-bit audio

    async def preload_phrases(self, phrases: Iterable[str], voice: str | None = "F1") -> None:
        """Synthesise each phrase once; `speak_text` then plays it from memory, with no TTS wait."""
        for phrase in phrases:
            if (phrase, voice) not in self.audio_cache:
                pcm, *_ = await self.speak_text(phrase, voice=voice)
                self.audio_cache[(phrase, voice)] = bytes(pcm)

    async def measure_room(self, seconds: float = 1.0) -> float | None:
        """Listen to the room for `seconds` and remember its noise level (the 10th percentile of
        the block levels, as the pause detector computes it), so the first turn does not run on
        `DEFAULT_NOISE_HINT`. Returns the level, or None without a microphone."""
        import numpy as np

        if self.hub is None:
            return None
        self.hub.drain()  # what piled up since the stream opened is old
        want = max(int(seconds * 1000 / self.hub.block_ms), 1)
        levels: list[float] = []
        try:
            while len(levels) < want:
                block = await asyncio.wait_for(self.hub.get(), timeout=1.0)
                x = np.frombuffer(block, dtype=np.int16).astype(np.float32) / 32768.0
                levels.append(float(np.sqrt(np.mean(x**2))) if x.size else 0.0)
        except TimeoutError:
            pass
        if not levels:
            return None
        self._noise.value = float(np.percentile(levels, 10))
        self.hub.drain()
        return self._noise.value

    def _play_cached(
        self, pcm: bytes, t_speech_end: float | None, on_chunk: Callable[[bytes], None] | None
    ) -> tuple[bytes, float, float, float]:
        """`speak_text` for a preloaded phrase: hand the audio on in 100 ms slices, as a stream
        would. Returns the same tuple as `speak_text`."""
        t0 = time.perf_counter()
        if on_chunk is not None:
            step = self.tts_sample_rate // 10 * 2
            for i in range(0, len(pcm), step):
                on_chunk(pcm[i : i + step])
        first_ms = (time.perf_counter() - t0) * 1000.0
        to_first = first_ms if t_speech_end is None else (t0 - t_speech_end) * 1000.0
        return pcm, first_ms, first_ms, to_first

    @property
    def noise_floor(self) -> float | None:
        """The room's background level (RMS, 0-1) measured while you were quiet, once known."""
        return self._noise.value

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

    async def _drain(self, quiet_s: float = 0.03) -> None:
        """Throw away anything the server sent that nobody read (a late end-of-turn)."""
        while True:
            try:
                await asyncio.wait_for(self._stt_ws.recv(), timeout=quiet_s)
            except TimeoutError:
                return

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
        await self._drain()
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
        if self.hub is not None:
            self.hub.drain()  # whatever piled up while nobody listened is the assistant's echo
            async for block in self.hub.blocks():
                yield block
            return
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
            device=self.mic_device,
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
        hint = self._noise.value if self._noise.value is not None else room_noise_hint()
        detector = PauseDetector(vad_hold, noise=hint)
        state: dict[str, float | None] = {"last_voiced": None, "finalize_sent": None}
        partial = [""]  # the latest interim text
        sent = bytearray()  # every block sent, so the turn's audio can be joined with a follow-up
        t_start = time.perf_counter()
        source = audio if audio is not None else self._mic_chunks(rate, block_ms)

        self.last_voiced = 0.0  # no speech yet in this listen (a notice may be spoken now)
        awaiting = [False]  # finalize sent, final not back yet: hold audio instead of sending it
        held: list[bytes] = []

        async def pump() -> None:
            async for chunk in source:
                x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                level = float(np.sqrt(np.mean(x**2))) if x.size else 0.0
                voiced, pause = detector.feed(level, len(chunk) / 2 / rate)
                if voiced:
                    state["last_voiced"] = self.last_voiced = time.perf_counter()
                if awaiting[0]:
                    # audio sent now would start a new stream on the server whose end-of-turn
                    # would arrive later as a stale transcript; it is released only if the
                    # server's answer turns out to be empty (nothing was said)
                    held.append(chunk)
                    continue
                for old in held:
                    await ws.send(old)
                    sent.extend(old)
                held.clear()
                await ws.send(chunk)
                sent.extend(chunk)
                if pause and vad_hold > 0:
                    state["finalize_sent"] = time.perf_counter()
                    mark("vad_end")
                    awaiting[0] = True
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
                        mark("asr_final", text=text)
                        own = bytes(
                            sent
                        )  # nothing is sent once finalize is out, so this is the turn
                        return HeardTurn(
                            text,
                            parsed.raw_text,
                            parsed.repair,
                            state["last_voiced"] or now,
                            now,
                            state["finalize_sent"],
                            own,
                            partial[0],
                            b"".join(held),
                        )
                    if parsed.end_of_turn:
                        state["finalize_sent"] = None  # nothing was said: keep listening
                        while held:  # release what was held back, in order, then carry on
                            block = held.pop(0)
                            await ws.send(block)
                            sent.extend(block)
                        awaiting[0] = False
                    elif text:
                        partial[0] = text
                        if on_partial is not None:
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
        cached = self.audio_cache.get((text, voice))
        if cached is not None:
            return self._play_cached(cached, t_speech_end, on_chunk)
        if self._tts_ws is None:
            raise RuntimeError("TTS socket not connected")

        self._active_speak_task = asyncio.current_task()
        try:
            self._current_context += 1
            ctx = self._current_context

            if t_speech_end is not None:  # the first sentence of the turn
                mark("tts_request")
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
                        if t_speech_end is not None:
                            mark("tts_first_audio")
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

    async def send_tts_cancel(self) -> None:
        """Tell the speech server to stop synthesising the current context (no task is touched)."""
        if self._tts_ws is not None and self._current_context:
            await self._tts_ws.send(CancelMessage(context=self._current_context).to_json())

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
