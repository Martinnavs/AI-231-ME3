"""Hearing the user start to speak while the assistant is thinking or talking.

`OnsetDetector` says when sound has been loud enough for long enough to be speech (a click or a
cough is not). `PreRoll` keeps the last second or so of microphone blocks, so the start of the
interruption is not lost while the detector makes up its mind.
"""

from __future__ import annotations

import difflib
import re
import statistics
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

    def feed(self, level: float, dur_s: float, extra: float = 0.0) -> bool:
        """``extra`` raises the bar for this block (the expected echo of the assistant's voice)."""
        if level > max(self.threshold, extra):
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


class EchoModel:
    """How much of the assistant's own voice comes back through the microphone.

    While it speaks, the loudness of what the microphone hears is compared with the loudness of
    what is being played. The typical ratio (the coupling: 0.02 with a headset, 0.2-0.5 with
    laptop speakers) times the loudness now is the echo to expect; the user is speaking if the
    microphone is clearly above that. Blocks that may be the user (above the bar) are not used to
    learn, so the user's own voice does not raise the estimate. Until enough blocks have been
    seen, ``prior`` is assumed, which is cautious.
    """

    def __init__(
        self, prior: float = 0.5, margin: float = 1.8, window: int = 15, min_samples: int = 4
    ) -> None:
        self.prior = prior
        self.margin = margin
        self.min_samples = min_samples
        self._ratios: deque[float] = deque(maxlen=window)

    @property
    def coupling(self) -> float:
        if len(self._ratios) < self.min_samples:
            return self.prior
        return statistics.median(self._ratios)

    def expected(self, played_level: float) -> float:
        """The microphone level above which a block is more than the echo."""
        return self.margin * self.coupling * played_level

    def learn(self, mic_level: float, played_level: float) -> None:
        if played_level > 0.01:
            self._ratios.append(mic_level / played_level)


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def looks_like_echo(heard: str, spoken: str, min_words: int = 3, share: float = 0.8) -> bool:
    """The 'user' said (nearly) the assistant's own words: its voice came back through the mic.

    True if a run of at least ``min_words`` words of what was heard appears, in order, in what
    the assistant was saying, and that run is most of what was heard.
    """
    a, b = _words(heard), _words(spoken)
    if len(a) < min_words or not b:
        return False
    match = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b)
    )
    return match.size >= min_words and match.size / len(a) >= share
