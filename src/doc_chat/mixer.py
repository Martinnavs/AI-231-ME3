"""The Pi speaker's mixer (Y3): replies and music through ONE output stream.

`pi_speaker.py` used to open a sound-card stream per reply. With music playing under the replies
two streams would have to share the USB card (not known to work on this Pi), so a single
`Mixer` owns the stream and adds up what the two sources deliver, 50 ms at a time:

* the reply channel (`tts_channel()`): the old `Player` interface (push, abort, close, set_gain);
  abort goes silent within one slice with an 8 ms fade, close waits until it has all been played;
* the music channel (`music_push`, `music_gain`, `music_flush`): a short ring buffer the laptop
  keeps topped up in real time; when it runs dry the music is silent for that slice, when it
  overflows the oldest audio is dropped.

The output is opened when the first audio arrives and closed after IDLE_CLOSE_S of nothing, so the
card is free otherwise. `sink_factory(rate)` returns (write, close); tests pass a list.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

SLICE_S = 0.05
FADE_S = 0.008
IDLE_CLOSE_S = 1.0
MUSIC_BUFFER_S = 1.0

SinkFactory = Callable[[int], tuple[Callable[[bytes], Any], Callable[[], Any]]]


def sounddevice_sink(rate: int) -> tuple[Callable[[bytes], Any], Callable[[], Any]]:
    import sounddevice as sd

    stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
    stream.start()

    def close() -> None:
        with contextlib.suppress(Exception):
            stream.stop()
            stream.close()

    return stream.write, close


class TtsChannel:
    """`Player`'s interface for one reply, played through the mixer."""

    def __init__(self, mixer: Mixer) -> None:
        self._mixer = mixer
        self.underruns = 0
        self._ended = False

    def push(self, pcm: bytes) -> None:
        self._mixer._tts_push(self, pcm)

    def set_gain(self, db: float) -> None:
        self._mixer._tts_gain = 10 ** (db / 20)

    def abort(self) -> None:
        self._mixer._tts_abort(self)

    def close(self) -> None:
        """Return when everything pushed has been played (or at once after `abort`)."""
        self._mixer._tts_wait(self)


class Mixer:
    def __init__(self, rate: int, sink_factory: SinkFactory = sounddevice_sink) -> None:
        self.rate = rate
        self._factory = sink_factory
        self._slice = max(int(rate * SLICE_S), 1) * 2  # bytes of 16-bit mono
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._tts = bytearray()
        self._tts_owner: TtsChannel | None = None
        self._tts_gain = 1.0
        self._tts_applied = 1.0
        self._tts_last = 0
        self._fade = False
        self._music = bytearray()
        self._music_gain = 1.0
        self._music_applied = 1.0
        self.music_underruns = 0
        self._sink: tuple[Callable[[bytes], Any], Callable[[], Any]] | None = None
        self._idle_since: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- the reply channel
    def tts_channel(self) -> TtsChannel:
        channel = TtsChannel(self)
        with self._lock:
            self._tts_owner = channel
            self._tts_gain = 1.0
        return channel

    def _tts_push(self, channel: TtsChannel, pcm: bytes) -> None:
        with self._lock:
            if channel is self._tts_owner:
                self._tts += pcm

    def _tts_abort(self, channel: TtsChannel) -> None:
        with self._lock:
            if channel is self._tts_owner:
                self._tts.clear()
                self._fade = True

    def _tts_wait(self, channel: TtsChannel) -> None:
        deadline = time.monotonic() + len(self._tts) / 2 / self.rate + 2.0
        with self._cond:
            while channel is self._tts_owner and self._tts and time.monotonic() < deadline:
                self._cond.wait(0.05)
            if channel is self._tts_owner:
                self._tts_owner = None

    # ---- the music channel
    def music_push(self, pcm: bytes) -> None:
        with self._lock:
            self._music += pcm
            limit = int(self.rate * MUSIC_BUFFER_S) * 2
            if len(self._music) > limit:
                del self._music[: len(self._music) - limit // 2 * 2]

    def music_set_gain(self, db: float) -> None:
        self._music_gain = 10 ** (db / 20)

    def music_flush(self) -> None:
        with self._lock:
            self._music.clear()

    # ---- the output
    def _take(self, buf: bytearray) -> np.ndarray | None:
        if not buf:
            return None
        piece = bytes(buf[: self._slice])
        del buf[: len(piece)]
        x = np.frombuffer(piece[: len(piece) // 2 * 2], dtype=np.int16).astype(np.float32)
        out = np.zeros(self._slice // 2, dtype=np.float32)
        out[: len(x)] = x
        return out

    def step(self) -> bytes | None:
        """The next 50 ms of output, or None when there is nothing to play."""
        n = self._slice // 2
        with self._cond:
            tts = self._take(self._tts)
            music = self._take(self._music)
            fade, self._fade = self._fade, False
            self._cond.notify_all()
        out = np.zeros(n, dtype=np.float32)
        if tts is not None:
            out += tts * np.linspace(self._tts_applied, self._tts_gain, n, dtype=np.float32)
            self._tts_applied = self._tts_gain
            self._tts_last = int(out[-1])
        elif fade and self._tts_last:
            m = max(int(self.rate * FADE_S), 1)
            out[:m] = self._tts_last * np.linspace(1.0, 0.0, m)
            self._tts_last = 0
        if music is not None:
            out += music * np.linspace(self._music_applied, self._music_gain, n, dtype=np.float32)
            self._music_applied = self._music_gain
        elif self._music_applied != self._music_gain:
            self._music_applied = self._music_gain
        if tts is None and music is None and not (fade and out.any()):
            return None
        return _soft_limit(out).astype(np.int16).tobytes()

    def run_once(self, now: float | None = None) -> bool:
        """One iteration of the writer: open the output if needed, write a slice, close after a
        quiet second. True if it wrote."""
        data = self.step()
        now = time.monotonic() if now is None else now
        if data is None:
            if self._sink is not None:
                self._idle_since = self._idle_since or now
                if now - self._idle_since >= IDLE_CLOSE_S:
                    self._sink[1]()
                    self._sink, self._idle_since = None, None
            return False
        if self._sink is None:
            self._sink = self._factory(self.rate)
        self._idle_since = None
        self._sink[0](data)  # blocks for the slice's duration on a real device
        return True

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                if not self.run_once():
                    time.sleep(SLICE_S / 2)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._sink is not None:
            self._sink[1]()
            self._sink = None


def _soft_limit(x: np.ndarray, knee: float = 24576.0) -> np.ndarray:
    """Samples above the knee are bent smoothly towards full scale instead of clipped (music at
    up to +6 dB would otherwise distort on its peaks)."""
    top = 32767.0 - knee
    over = np.abs(x) > knee
    if over.any():
        x = x.copy()
        x[over] = np.sign(x[over]) * (knee + top * np.tanh((np.abs(x[over]) - knee) / top))
    limited: np.ndarray = np.clip(x, -32768, 32767)
    return limited
