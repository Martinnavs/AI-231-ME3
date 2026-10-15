from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt


class AudioFileError(Exception):
    pass


@dataclass(frozen=True)
class AudioClip:
    samples: npt.NDArray[np.float32]
    sample_rate: int

    @property
    def duration_s(self) -> float:
        return float(len(self.samples)) / self.sample_rate


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None
    is_final: bool
    t_audio_end_s: float | None
    end_of_turn: bool = False


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str | None
