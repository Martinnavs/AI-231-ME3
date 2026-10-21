"""Hearing the user start to speak while the assistant is thinking or talking.

`OnsetDetector` says when sound has been loud enough for long enough to be speech (a click or a
cough is not). `PreRoll` keeps the last second or so of microphone blocks, so the start of the
interruption is not lost while the detector makes up its mind.
"""

from __future__ import annotations

from collections import deque

SAMPLE_RATE = 16000


class OnsetDetector:
    """True once the level has been above ``max(noise * ratio, floor)`` for ``min_s`` of speech.

    The count leaks while the level is low (a word's quiet moments do not reset it), and rises
    only while it is high, so a short burst never adds up to an onset.
    """

    def __init__(
        self, noise: float, ratio: float = 4.0, floor: float = 0.02, min_s: float = 0.3
    ) -> None:
        self.noise = noise
        self.ratio = ratio
        self.floor = floor
        self.min_s = min_s
        self._heard_s = 0.0

    @property
    def threshold(self) -> float:
        return max(self.noise * self.ratio, self.floor)

    def feed(self, level: float, dur_s: float) -> bool:
        if level > self.threshold:
            self._heard_s += dur_s
        else:
            self._heard_s = max(self._heard_s - dur_s, 0.0)
        return self._heard_s >= self.min_s

    def reset(self) -> None:
        self._heard_s = 0.0


class PreRoll:
    """The most recent ``seconds`` of 16-bit microphone blocks."""

    def __init__(self, seconds: float = 1.2, block_s: float = 0.1) -> None:
        self._blocks: deque[bytes] = deque(maxlen=max(round(seconds / block_s), 1))

    def push(self, block: bytes) -> None:
        self._blocks.append(block)

    def blocks(self) -> list[bytes]:
        return list(self._blocks)
