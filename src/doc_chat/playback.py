"""Non-blocking speaker output, so synthesis of the next sentence overlaps playback."""

from __future__ import annotations

import contextlib
import queue
import threading
from typing import Any

SLICE_S = 0.05  # audio is handed to the device in pieces this long, so a stop takes effect fast


class Player:
    """Writes PCM to the speakers from a background thread; `close` waits for it to finish.

    `abort` goes silent within one slice (50 ms). It must not touch the audio stream itself:
    PortAudio's ALSA backend deadlocks, and prints a wall of "Expression ... failed" errors, if a
    stream is aborted from one thread while another is blocked writing to it. So `abort` only sets
    a flag; the writing thread notices it between slices and aborts the stream itself.
    """

    def __init__(self, sample_rate: int) -> None:
        import sounddevice as sd

        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._aborted = threading.Event()
        self._slice = max(int(sample_rate * SLICE_S), 1) * 2  # bytes of 16-bit mono
        self._stream: Any = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")
        self._stream.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        stopped = False

        def stop_device() -> None:
            nonlocal stopped
            if not stopped:
                stopped = True
                with contextlib.suppress(Exception):
                    self._stream.abort()  # from this thread, the one that writes

        while (pcm := self._queue.get()) is not None:
            for i in range(0, len(pcm), self._slice):
                if self._aborted.is_set():
                    break
                try:
                    self._stream.write(pcm[i : i + self._slice])
                except Exception:
                    self._aborted.set()
                    break
            if self._aborted.is_set():
                stop_device()
        if self._aborted.is_set():
            stop_device()

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
            self._stream.stop()
            self._stream.close()
