"""Find speech and pauses in a stream of audio levels, with a noise floor measured from the room.

Used by the test client (stand-in for the Pi's VAD) and by the Parakeet stream to start decoding
at the first short pause. Speech is anything well above the room's noise floor, so steady hum or
a fan does not count as speech.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

import numpy as np


@dataclass
class NoiseFloor:
    """The room's noise level, passed from one turn to the next so a turn can start listening for
    pauses at once instead of spending its first second measuring the room."""

    value: float | None = None


class PauseDetector:
    """Feed it one level per chunk; it says when there is speech and when a pause is long enough.

    ``hold_s`` of quiet after speech is a pause (reported once). The noise floor is the 10th
    percentile of the last ~100 quiet chunks. Without a ``noise`` hint the first second only
    measures the room; with one (carried over from the previous turn) it starts at once.
    """

    def __init__(
        self,
        hold_s: float,
        ratio: float = 2.5,
        floor: float = 0.008,
        noise: float | None = None,
        calibration_s: float = 1.0,
        resume_ratio: float = 1.5,
    ) -> None:
        self.hold_s = hold_s
        self.ratio = ratio
        self.resume_ratio = resume_ratio
        self.floor = floor
        self.levels: collections.deque[float] = collections.deque(maxlen=100)
        self.heard = False
        self.quiet_s = 0.0
        self._hint = noise
        self._calibration_left = 0.0 if noise is not None else calibration_s

    @property
    def noise(self) -> float:
        if len(self.levels) >= 20 or (self._hint is None and self.levels):
            return float(np.percentile(self.levels, 10))
        return self._hint or 0.0

    @property
    def threshold(self) -> float:
        return max(self.floor, self.ratio * self.noise)

    @property
    def resume_threshold(self) -> float:
        """Lower than ``threshold``: after a pause, anything this far above the noise floor may be
        the soft tail of a word (the pause was declared inside it), not the room."""
        return max(self.floor, self.resume_ratio * self.noise)

    def above_floor(self, rms: float) -> bool:
        return rms > self.resume_threshold

    @property
    def measured(self) -> bool:
        """Enough quiet audio has been heard for ``noise`` to describe the room."""
        return len(self.levels) >= 20

    def feed(self, rms: float, dur_s: float = 0.1) -> tuple[bool, bool]:
        """One chunk's RMS level -> (is speech, the pause is long enough)."""
        calibrating = self._calibration_left > 0
        if calibrating:
            self._calibration_left -= dur_s
        voiced = not calibrating and rms > self.threshold
        if not voiced:
            self.levels.append(rms)  # speech is kept out of the noise estimate
        if voiced:
            self.heard, self.quiet_s = True, 0.0
            return True, False
        if self.heard:
            self.quiet_s += dur_s
            if self.hold_s > 0 and self.quiet_s >= self.hold_s:
                self.heard, self.quiet_s = False, 0.0
                return False, True
        return False, False
