from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

import numpy as np

from .base import Synthesizer, Transcriber, TranscriptionStream
from .types import AudioClip, Transcript, Voice
from .wav import float_to_pcm16, pcm16_to_float, resample

_SPEECH_DBFS = -40.0
_WINDOW_MS = 10
_CLOSED = object()


class _FakeStream(TranscriptionStream):
    def __init__(self, owner: FakeTranscriber) -> None:
        self._o = owner
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._audio_s = 0.0
        self._interim_s = 0.0
        self._interims = 0
        self._tail = b""
        self._speech_seen = False
        self._quiet_ms = 0.0
        self._finals = 0

    async def start(self) -> None:
        return None

    def _script_text(self) -> str:
        script = self._o.script
        return script[min(self._finals, len(script) - 1)]

    async def send(self, pcm: bytes) -> None:
        data = self._tail + pcm
        win = int(self._o.sample_rate * _WINDOW_MS / 1000) * 2
        usable = len(data) - len(data) % win
        self._tail = data[usable:]
        for i in range(0, usable, win):
            self._on_window(pcm16_to_float(data[i : i + win]))
        await asyncio.sleep(0)

    def _on_window(self, w: np.ndarray) -> None:
        self._audio_s += _WINDOW_MS / 1000
        rms = float(np.sqrt(np.mean(np.square(w)))) if len(w) else 0.0
        dbfs = 20 * np.log10(rms) if rms > 0 else -120.0
        step = self._o.interim_every_ms / 1000
        if self._audio_s - self._interim_s >= step - 1e-9:
            self._interim_s = self._audio_s
            words = self._script_text().split()
            part = " ".join(words[: 1 + self._interims % max(len(words), 1)])
            self._interims += 1
            self._queue.put_nowait(Transcript(part, "en", False, self._audio_s))
        eot = self._o.eot_silence_ms
        if eot is None:
            return
        if dbfs > _SPEECH_DBFS:
            self._speech_seen = True
            self._quiet_ms = 0.0
        elif self._speech_seen:
            self._quiet_ms += _WINDOW_MS
            if self._quiet_ms >= eot - 1e-9:
                self._speech_seen = False
                self._quiet_ms = 0.0
                self._queue.put_nowait(Transcript("", "en", False, self._audio_s, end_of_turn=True))

    async def finalize(self) -> None:
        if self._o.latency_ms:
            await asyncio.sleep(self._o.latency_ms / 1000)
        text = self._script_text()
        self._finals += 1
        self._queue.put_nowait(Transcript(text, "en", True, self._audio_s))

    async def _iter(self) -> AsyncIterator[Transcript]:
        while True:
            item = await self._queue.get()
            if item is _CLOSED:
                return
            assert isinstance(item, Transcript)
            yield item

    def results(self) -> AsyncIterator[Transcript]:
        return self._iter()

    async def aclose(self) -> None:
        self._queue.put_nowait(_CLOSED)


class FakeTranscriber(Transcriber):
    def __init__(
        self,
        script: Sequence[str] = ("hello",),
        interim_every_ms: int = 100,
        latency_ms: int = 0,
        eot_silence_ms: int | None = 300,
    ) -> None:
        self.sample_rate = 16000
        self.script = tuple(script)
        self.interim_every_ms = interim_every_ms
        self.latency_ms = latency_ms
        self.eot_silence_ms = eot_silence_ms
        self._batch_calls = 0

    async def transcribe(self, clip: AudioClip) -> Transcript:
        clip = resample(clip, self.sample_rate)
        text = self.script[min(self._batch_calls, len(self.script) - 1)]
        self._batch_calls += 1
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        return Transcript(text, "en", True, clip.duration_s)

    def open_stream(self) -> TranscriptionStream:
        return _FakeStream(self)


class FakeSynthesizer(Synthesizer):
    def __init__(
        self,
        sample_rate: int = 16000,
        ms_per_char: int = 60,
        chunk_ms: int = 20,
        first_chunk_latency_ms: int = 0,
    ) -> None:
        self.sample_rate = sample_rate
        self.ms_per_char = ms_per_char
        self.chunk_ms = chunk_ms
        self.first_chunk_latency_ms = first_chunk_latency_ms
        self._generation = 0

    def _tone(self, start: int, n: int) -> np.ndarray:
        t = np.arange(start, start + n) / self.sample_rate
        return (10 ** (-12 / 20) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def _total(self, text: str) -> int:
        return len(text) * self.ms_per_char * self.sample_rate // 1000

    async def synthesize(self, text: str, voice: str | None = None) -> AudioClip:
        return AudioClip(self._tone(0, self._total(text)), self.sample_rate)

    async def stream_synthesize(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        total = self._total(text)
        if total == 0:
            return
        generation = self._generation
        if self.first_chunk_latency_ms:
            await asyncio.sleep(self.first_chunk_latency_ms / 1000)
        step = self.sample_rate * self.chunk_ms // 1000
        for start in range(0, total, step):
            if generation != self._generation:
                return
            yield float_to_pcm16(self._tone(start, min(step, total - start)))
            await asyncio.sleep(0)

    async def list_voices(self) -> list[Voice]:
        return [Voice(id="fake", name="Fake tone", language="en")]

    async def cancel(self) -> None:
        self._generation += 1
