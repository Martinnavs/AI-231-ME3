"""Non-blocking speaker output, so synthesis of the next sentence overlaps playback."""

from __future__ import annotations

import queue
import threading
from typing import Any


class Player:
    """Writes PCM to the speakers from a background thread; `close` waits for it to finish."""

    def __init__(self, sample_rate: int) -> None:
        import sounddevice as sd

        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._stream: Any = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")
        self._stream.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while (pcm := self._queue.get()) is not None:
            self._stream.write(pcm)

    def push(self, pcm: bytes) -> None:
        self._queue.put(pcm)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()
        self._stream.stop()
        self._stream.close()
