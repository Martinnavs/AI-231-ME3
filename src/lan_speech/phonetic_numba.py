"""Optional numba kernel for the name search of ``transcript_repair`` (``EntityMatcher.nearest``).

It runs the same weighted edit distance as ``phonetic_distance`` over every listed name in one
call, in float64 with the same operations in the same order, so the numbers are bit-identical to
the pure-Python path. Without numba (or with ``LAN_SPEECH_NO_NUMBA`` set) ``AVAILABLE`` is False
and the callers keep the Python code.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import numpy as np

try:
    if os.environ.get("LAN_SPEECH_NO_NUMBA"):
        raise ImportError("disabled by LAN_SPEECH_NO_NUMBA")
    from numba import njit

    AVAILABLE = True
except ImportError:
    AVAILABLE = False


def distances_py(sound: Any, codes: Any, offsets: Any, sub: Any, gap: Any, out: Any) -> None:
    """``out[k]`` = distance from ``sound`` to name k, or -1 when its length rules it out.

    ``codes``/``offsets`` hold every name's phoneme codes back to back; ``sub[a, b]`` and
    ``gap[a]`` are the substitution and insertion costs by code."""
    n = len(sound)
    width = int(np.max(offsets[1:] - offsets[:-1])) + 1 if len(offsets) > 1 else 1
    prev = np.empty(width, dtype=np.float64)
    cur = np.empty(width, dtype=np.float64)
    for k in range(len(offsets) - 1):
        start = offsets[k]
        m = offsets[k + 1] - start
        longest = n if n > m else m
        if abs(m - n) > max(1, int(0.25 * longest)):
            out[k] = -1.0
            continue
        prev[0] = 0.0
        for j in range(1, m + 1):
            prev[j] = prev[j - 1] + gap[codes[start + j - 1]]
        for i in range(n):
            ca = sound[i]
            g = gap[ca]
            cur[0] = prev[0] + g
            for j in range(1, m + 1):
                cb = codes[start + j - 1]
                best = prev[j] + g
                other = cur[j - 1] + gap[cb]
                if other < best:
                    best = other
                other = prev[j - 1] + sub[ca, cb]
                if other < best:
                    best = other
                cur[j] = best
            for j in range(m + 1):
                prev[j] = cur[j]
        out[k] = prev[m] / longest


distances: Callable[..., None] = distances_py
if AVAILABLE:
    distances = njit(cache=True)(distances_py)


def warm_up() -> None:
    """Compile the kernel now (about a second the first time, then from the on-disk cache)."""
    if not AVAILABLE:
        return
    sub = np.zeros((128, 128))
    gap = np.ones(128)
    out = np.empty(2)
    codes = np.array([97, 98, 99, 97, 98, 99, 100], dtype=np.int32)
    offsets = np.array([0, 3, 7], dtype=np.int64)
    distances(np.array([97, 98, 99], dtype=np.int32), codes, offsets, sub, gap, out)
