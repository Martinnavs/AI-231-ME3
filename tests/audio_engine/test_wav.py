from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from audio_engine.types import AudioClip, AudioFileError
from audio_engine.wav import (
    float_to_pcm16,
    pcm16_to_float,
    read_wav,
    resample,
    write_wav,
)


def _tone(rate: int, hz: float = 1000.0, secs: float = 0.5) -> AudioClip:
    t = np.arange(int(rate * secs)) / rate
    return AudioClip((0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32), rate)


def _rms_db(x: np.ndarray) -> float:  # type: ignore[type-arg]
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x)))))


def test_pcm_roundtrip_and_resample() -> None:
    clip = _tone(16000)
    back = pcm16_to_float(float_to_pcm16(clip.samples))
    assert np.max(np.abs(back - clip.samples)) < 2 / 32768
    up = resample(clip, 48000)
    down = resample(up, 16000)
    assert up.sample_rate == 48000 and len(up.samples) == 3 * len(clip.samples)
    mid = slice(1000, -1000)
    assert abs(_rms_db(down.samples[mid]) - _rms_db(clip.samples[mid])) < 0.5
    with pytest.raises(ValueError):
        pcm16_to_float(b"\x00\x01\x02")
    assert len(float_to_pcm16(np.array([2.0, -2.0], dtype=np.float32))) == 4


def test_wav_read_write_stereo_downmix(tmp_path: Path) -> None:
    clip = _tone(16000)
    write_wav(tmp_path / "a.wav", clip)
    got = read_wav(tmp_path / "a.wav")
    assert got.sample_rate == 16000 and abs(got.duration_s - 0.5) < 1e-6
    stereo = np.stack([clip.samples, np.zeros_like(clip.samples)], axis=1)
    sf.write(str(tmp_path / "s.wav"), stereo, 16000)
    mono = read_wav(tmp_path / "s.wav")
    assert mono.samples.ndim == 1
    assert np.allclose(mono.samples, clip.samples / 2, atol=1e-3)
    with pytest.raises(AudioFileError, match="missing.wav"):
        read_wav(tmp_path / "missing.wav")
