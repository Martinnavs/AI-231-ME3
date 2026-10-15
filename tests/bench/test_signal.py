from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from bench.signal import (
    frame_rms_dbfs,
    longest_internal_gap_s,
    make_chirp,
    noise_burst,
    speech_bounds,
)

SR = 16000
Arr = npt.NDArray[np.float32]


def tone(dur_s: float, amp: float = 0.5, f: float = 220.0) -> Arr:
    t = np.arange(round(dur_s * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def silence(dur_s: float) -> Arr:
    return np.zeros(round(dur_s * SR), dtype=np.float32)


def test_frame_rms_and_chirp() -> None:
    db = frame_rms_dbfs(tone(1.0, amp=0.5, f=1000.0), SR)
    expected = 20 * np.log10(0.5 / np.sqrt(2))
    assert np.all(np.abs(db[2:-2] - expected) < 0.2)
    assert frame_rms_dbfs(silence(0.1), SR).max() == -120.0

    c = make_chirp(SR)
    assert len(c) == SR // 2
    assert c.dtype == np.float32
    assert float(np.max(np.abs(c))) <= 0.5 + 1e-6

    burst = noise_burst(SR, 150, seed=1, level_dbfs=-12.0)
    rms_db = 20 * np.log10(float(np.sqrt(np.mean(burst**2))))
    assert abs(rms_db + 12.0) < 0.2
    assert np.array_equal(burst, noise_burst(SR, 150, seed=1))
    assert not np.array_equal(burst, noise_burst(SR, 150, seed=2))


def _check(x: Arr, true_start: float, true_end: float) -> None:
    start, end = speech_bounds(x, SR)
    assert abs(end - true_end) <= 0.010
    assert abs(start - true_start) <= 0.010


def test_speech_bounds_accuracy() -> None:
    pad = silence(0.4)

    _check(np.concatenate([pad, tone(1.0), pad]), 0.4, 1.4)

    tau = 0.010
    t = np.arange(round(0.2 * SR)) / SR
    tail = (0.5 * np.exp(-t / tau) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    true_end = 0.4 + 1.0 + tau * np.log(1 / 0.0178)
    _check(np.concatenate([pad, tone(1.0), tail, pad]), 0.4, float(true_end))

    rng = np.random.default_rng(0)
    sigma = 0.5 * 10 ** (-30 / 20) / np.sqrt(2)
    fric = (rng.standard_normal(round(0.08 * SR)) * sigma).astype(np.float32)
    _check(np.concatenate([pad, tone(1.0), fric, pad]), 0.4, 0.4 + 1.0 + 0.08)

    words = np.concatenate([pad, tone(0.4), silence(0.3), tone(0.5), pad])
    _check(words, 0.4, 0.4 + 0.4 + 0.3 + 0.5)


def test_speech_bounds_silent_raises() -> None:
    with pytest.raises(ValueError, match="silent"):
        speech_bounds(silence(0.5), SR)


def test_longest_internal_gap() -> None:
    x = np.concatenate([silence(0.5), tone(0.4), silence(0.7), tone(0.4), silence(2.0)])
    assert abs(longest_internal_gap_s(x, SR) - 0.7) <= 0.015
    assert longest_internal_gap_s(tone(0.5), SR) == 0.0
