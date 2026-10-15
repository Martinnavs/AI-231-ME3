from __future__ import annotations

from math import gcd
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf
from scipy.signal import resample_poly

from .types import AudioClip, AudioFileError


def pcm16_to_float(pcm: bytes) -> npt.NDArray[np.float32]:
    if len(pcm) % 2:
        raise ValueError(f"corrupt PCM frame: odd byte length {len(pcm)}")
    return (np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0).astype(np.float32)


def float_to_pcm16(samples: npt.NDArray[np.float32]) -> bytes:
    scaled = np.rint(np.clip(samples, -1.0, 1.0) * 32768.0)
    return np.clip(scaled, -32768, 32767).astype("<i2").tobytes()


def read_wav(path: Path) -> AudioClip:
    try:
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except (OSError, RuntimeError, sf.LibsndfileError) as exc:
        raise AudioFileError(f"cannot read audio file {path}: {exc}") from exc
    mono = data.mean(axis=1).astype(np.float32)
    return AudioClip(samples=mono, sample_rate=int(rate))


def write_wav(path: Path, clip: AudioClip) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), clip.samples, clip.sample_rate, subtype="PCM_16")


def resample(clip: AudioClip, rate: int) -> AudioClip:
    if rate == clip.sample_rate:
        return clip
    g = gcd(rate, clip.sample_rate)
    out = resample_poly(clip.samples, rate // g, clip.sample_rate // g)
    return AudioClip(samples=out.astype(np.float32), sample_rate=rate)
