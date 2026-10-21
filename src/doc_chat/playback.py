"""Non-blocking speaker output, so synthesis of the next sentence overlaps playback."""

from __future__ import annotations

import contextlib
import queue
import threading
from typing import Any


class Player:
    """Writes PCM to the speakers from a background thread; `close` waits for it to finish."""

    def __init__(self, sample_rate: int) -> None:
        import sounddevice as sd

        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._aborted = False
        self._stream: Any = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")
        self._stream.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while (pcm := self._queue.get()) is not None:
            if self._aborted:
                continue
            try:
                self._stream.write(pcm)
            except Exception:  # the stream was aborted under us
                self._aborted = True

    def push(self, pcm: bytes) -> None:
        self._queue.put(pcm)

    def abort(self) -> None:
        """Go silent now: drop everything queued and cut the audio already handed to the device."""
        self._aborted = True
        with contextlib.suppress(queue.Empty):
            while True:
                self._queue.get_nowait()
        with contextlib.suppress(Exception):
            self._stream.abort()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            if not self._aborted:
                raise
