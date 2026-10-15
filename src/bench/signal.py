from __future__ import annotations

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import chirp

_FLOOR_DBFS = -120.0
_SILENT_DBFS = -90.0


def _frame_params(sr: int, frame_ms: float, hop_ms: float) -> tuple[int, int]:
    return max(1, round(sr * frame_ms / 1000)), max(1, round(sr * hop_ms / 1000))


def frame_rms_dbfs(
    x: npt.NDArray[np.float32], sr: int, frame_ms: float = 10.0, hop_ms: float = 5.0
) -> npt.NDArray[np.float64]:
    n, hop = _frame_params(sr, frame_ms, hop_ms)
    extra = max(n - len(x), 0) if len(x) < n else (-(len(x) - n)) % hop
    padded = np.concatenate([x.astype(np.float64), np.zeros(extra)])
    frames = sliding_window_view(padded, n)[::hop]
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(rms)
    return np.maximum(db, _FLOOR_DBFS)


def _active(db: npt.NDArray[np.float64], rel_db: float) -> npt.NDArray[np.bool_]:
    peak = float(db.max())
    if peak < _SILENT_DBFS:
        raise ValueError("no speech: clip is silent")
    return db >= peak + rel_db


def speech_bounds(
    x: npt.NDArray[np.float32], sr: int, *, rel_db: float = -35.0, hangover_ms: float = 50.0
) -> tuple[float, float]:
    # hangover_ms is part of the contract but cannot move first/last boundaries; kept for callers.
    del hangover_ms
    n, hop = _frame_params(sr, 10.0, 5.0)
    db = frame_rms_dbfs(x, sr)
    act = _active(db, rel_db)
    idx = np.flatnonzero(act)
    s0 = int(idx[0]) * hop
    e0 = min(int(idx[-1]) * hop + n, len(x))
    thr_amp = float(10 ** ((float(db.max()) + rel_db) / 20.0) * np.sqrt(2.0))
    hits = np.flatnonzero(np.abs(x[s0:e0]) >= thr_amp)
    if len(hits) == 0:
        return s0 / sr, e0 / sr
    return (s0 + int(hits[0])) / sr, (s0 + int(hits[-1]) + 1) / sr


def longest_internal_gap_s(x: npt.NDArray[np.float32], sr: int, *, rel_db: float = -35.0) -> float:
    _, hop = _frame_params(sr, 10.0, 5.0)
    act = _active(frame_rms_dbfs(x, sr), rel_db)
    idx = np.flatnonzero(act)
    inner = act[int(idx[0]) : int(idx[-1]) + 1]
    longest = run = 0
    for a in inner:
        run = 0 if a else run + 1
        longest = max(longest, run)
    return longest * hop / sr


def make_chirp(
    sr: int, duration_s: float = 0.5, f0: float = 500.0, f1: float = 4000.0
) -> npt.NDArray[np.float32]:
    t = np.arange(round(sr * duration_s)) / sr
    y = np.asarray(0.5 * chirp(t, f0=f0, t1=duration_s, f1=f1, method="linear"), dtype=np.float64)
    fade = min(len(y) // 2, round(sr * 0.005))
    if fade:
        ramp = np.linspace(0.0, 1.0, fade)
        y[:fade] *= ramp
        y[-fade:] *= ramp[::-1]
    return y.astype(np.float32)


def noise_burst(
    sr: int, duration_ms: float, seed: int, level_dbfs: float = -12.0
) -> npt.NDArray[np.float32]:
    n = round(sr * duration_ms / 1000)
    y = np.random.default_rng(seed).standard_normal(n)
    y *= 10 ** (level_dbfs / 20) / max(float(np.sqrt(np.mean(np.square(y)))), 1e-12)
    return np.clip(y, -1.0, 1.0).astype(np.float32)
