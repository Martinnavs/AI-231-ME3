"""A loudness gate against TV, radio and background speech: is this turn as loud as the user?

The user talks close to the microphone; a TV or a conversation across the room arrives much
quieter (fdbench's clips: user speech about -9 to -23 dBFS, background -26 to -31, TV/radio -32 to
-41). A turn more than `gap_db` quieter than the user's usual level is not for the assistant. The
usual level is measured the way `doc_chat.calibrate` measures the room (`block_levels`,
`noise_db`, `speech_db`) over the last `WINDOW_S` seconds of speech from turns the assistant
answered, so it follows the user's voice and the room as they change; until that window holds
`MIN_REF_S` seconds, the level from `out/calibration.json` stands in, and with neither the gate
lets everything through. Turns it drops never enter the window, so a TV cannot teach the gate
that it is the user. It cannot tell the user from another person who is as loud (side talk):
that is `doc_chat.addressee`'s job.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from doc_chat.calibrate import BLOCK_S, block_levels, dbfs, noise_db, speech_db

WINDOW_S = 30.0  # seconds of the user's speech the reference level is measured over
MIN_REF_S = 3.0  # speech needed in the window before it replaces the calibration level
MIN_TURN_S = 0.5  # a shorter turn is passed: too little audio to judge
DEFAULT_GAP_DB = 10.0


@dataclass(frozen=True)
class Verdict:
    far: bool  # quieter than the user by more than the gap: not meant for the assistant
    turn_db: float | None
    reference_db: float | None
    source: str  # "window", "calibration" or "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "far": self.far,
            "turn_db": None if self.turn_db is None else round(self.turn_db, 1),
            "reference_db": None if self.reference_db is None else round(self.reference_db, 1),
            "source": self.source,
        }


def active_levels(pcm: bytes) -> tuple[list[float], float | None]:
    """(the speech blocks' levels, the turn's speech level in dBFS) of 16 kHz 16-bit mono audio;
    ([], None) when it is too short. The speech blocks are those 10 dB above the turn's own
    noise floor, as `calibrate.speech_db` takes them; a turn with no quiet part (a TV, a crowd)
    has no such floor, so its speech is the blocks within 20 dB of its loudest."""
    levels = block_levels(pcm)
    if len(levels) * BLOCK_S < MIN_TURN_S:
        return [], None
    noise = noise_db(levels)
    floor = 10 ** ((noise + 10.0) / 20.0)
    active = [lv for lv in levels if lv >= floor]
    if active:
        return active, speech_db(levels, noise)
    near_top = [lv for lv in levels if lv >= max(levels) * 0.1]
    return near_top, dbfs(math.sqrt(sum(lv * lv for lv in near_top) / len(near_top)))


class LoudnessGate:
    def __init__(
        self,
        gap_db: float = DEFAULT_GAP_DB,
        window_s: float = WINDOW_S,
        calibration_db: float | None = None,
    ) -> None:
        self.gap_db = gap_db
        self.calibration_db = calibration_db
        self._max_blocks = max(int(window_s / BLOCK_S), 1)
        self._turns: deque[list[float]] = deque()  # the speech blocks of each answered turn
        self._blocks = 0

    def reference(self) -> tuple[float | None, str]:
        """(the user's usual level in dBFS, where it came from)."""
        if self._blocks * BLOCK_S >= MIN_REF_S:
            power = sum(lv * lv for turn in self._turns for lv in turn) / self._blocks
            return dbfs(math.sqrt(power)), "window"
        if self.calibration_db is not None:
            return self.calibration_db, "calibration"
        return None, "none"

    def check(self, pcm: bytes) -> Verdict:
        """Whether this turn is too quiet to be the user's. Learns nothing: see `accept`."""
        _, turn = active_levels(pcm)
        ref, source = self.reference()
        if turn is None or ref is None:
            return Verdict(False, turn, ref, source)
        return Verdict(turn < ref - self.gap_db, turn, ref, source)

    def accept(self, pcm: bytes) -> None:
        """Add the audio of a turn the assistant answered to the reference window."""
        active, _ = active_levels(pcm)
        if not active:
            return
        self._turns.append(active)
        self._blocks += len(active)
        while self._blocks > self._max_blocks and len(self._turns) > 1:
            self._blocks -= len(self._turns.popleft())
        if self._blocks > self._max_blocks:  # one long turn: keep its latest blocks
            self._turns[0] = self._turns[0][-self._max_blocks :]
            self._blocks = len(self._turns[0])
