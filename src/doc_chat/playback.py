"""Non-blocking speaker output, so synthesis of the next sentence overlaps playback."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections import deque
from typing import Any

import numpy as np

SLICE_S = 0.05  # audio is handed to the device in pieces this long, so a stop takes effect fast
FADE_S = 0.008  # a cut-off ends with a fade this long instead of a step (which is heard as a click)


class Player:
    """Writes PCM to the speakers from a background thread; `close` waits for it to finish.

    `abort` goes silent within one slice (50 ms) plus the device's own buffer (about 35 ms), and
    ends with an 8 ms fade, not a step. It must not touch the audio stream itself:
    PortAudio's ALSA backend deadlocks, and prints a wall of "Expression ... failed" errors, if a
    stream is aborted from one thread while another is blocked writing to it. So `abort` only sets
    a flag; the writing thread notices it between slices and aborts the stream itself.
    """

    def __init__(self, sample_rate: int) -> None:
        import sounddevice as sd

        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._aborted = threading.Event()
        self._levels: deque[tuple[float, float]] = deque(maxlen=64)  # (time, RMS) per slice
        self._slice = max(int(sample_rate * SLICE_S), 1) * 2  # bytes of 16-bit mono
        self._rate = sample_rate
        self.underruns = 0  # slices the device had to wait for (heard as a stutter)
        self._stream: Any = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")
        self._stream.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        stopped = False
        last = 0  # the last sample handed to the device

        def stop_device() -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            n = max(int(self._rate * FADE_S), 1)
            ramp = (last * np.linspace(1.0, 0.0, n)).astype(np.int16).tobytes()
            try:
                self._stream.write(ramp)  # from this thread, the one that writes
                self._stream.stop()  # lets the device play out its buffer (about 35 ms)
            except Exception:
                with contextlib.suppress(Exception):
                    self._stream.abort()

        while (pcm := self._queue.get()) is not None:
            for i in range(0, len(pcm), self._slice):
                if self._aborted.is_set():
                    break
                piece = pcm[i : i + self._slice]
                x = np.frombuffer(piece[: len(piece) // 2 * 2], dtype=np.int16)  # whole samples
                level = (
                    float(np.sqrt(np.mean((x.astype(np.float32) / 32768.0) ** 2)))
                    if x.size
                    else 0.0
                )
                self._levels.append((time.perf_counter(), level))
                try:
                    if self._stream.write(piece):
                        self.underruns += 1
                except Exception:
                    self._aborted.set()
                    break
                last = int(x[-1]) if x.size else last
            if self._aborted.is_set():
                stop_device()
        if self._aborted.is_set():
            stop_device()

    def level_recent(self, window_s: float = 0.3) -> float:
        """The loudest slice (RMS, 0-1) handed to the speaker in the last ``window_s`` seconds."""
        now = time.perf_counter()
        return max((lv for t, lv in list(self._levels) if now - t <= window_s), default=0.0)

    def push(self, pcm: bytes) -> None:
        self._queue.put(pcm)

    def abort(self) -> None:
        """Go silent now: drop everything queued; the writing thread stops the device."""
        self._aborted.set()
        with contextlib.suppress(queue.Empty):
            while True:
                self._queue.get_nowait()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=2.0)  # never wait on a stuck device for long
        with contextlib.suppress(Exception):
            if not self._aborted.is_set():  # a cut-off already stopped it, from the writing thread
                self._stream.stop()
            self._stream.close()
