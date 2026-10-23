"""scripts/guard_replay.py scoring: beeps and pushes are both seconds from the FIRST push."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "guard_replay.py"
spec = importlib.util.spec_from_file_location("guard_replay", SCRIPT)
assert spec and spec.loader
guard_replay = importlib.util.module_from_spec(spec)
sys.modules["guard_replay"] = guard_replay
spec.loader.exec_module(guard_replay)


def _trace(pushes: list[tuple[float, float]], beeps: list[float]) -> dict[str, np.ndarray]:
    t = np.arange(0, 40, 0.1)
    return {"t": t, "pushes": np.array(pushes), "beeps": np.array(beeps)}


def _fire_at(tr: dict[str, np.ndarray], *times: float) -> np.ndarray:
    fired = np.zeros(len(tr["t"]), dtype=bool)
    for x in times:
        fired[int(round(x * 10))] = True
    return fired


def test_hit_latency_is_measured_from_the_beep_of_a_later_clip() -> None:
    # clip 2 is pushed at 10 s and its beep sounds at 12.5 s (absolute); the guard fires at 13.2 s
    tr = _trace([(0.0, 5.0), (10.0, 5.0)], [2.5, 12.5])
    false, scored, lat = guard_replay.score(_fire_at(tr, 3.0, 13.2), tr)
    assert false == 0 and lat[0] == np.float64(lat[0]) and abs(lat[0] - 0.5) < 1e-6
    assert abs(lat[1] - 0.7) < 1e-6


def test_a_fire_before_the_beep_is_a_false_fire() -> None:
    tr = _trace([(0.0, 5.0)], [2.5])
    false, scored, lat = guard_replay.score(_fire_at(tr, 1.0, 3.0), tr)
    assert false == 1 and scored == 1  # the echo fired it before anyone could have spoken


def test_a_fire_in_an_echo_only_clip_is_a_false_fire_and_a_quiet_one_is_not() -> None:
    tr = _trace([(0.0, 5.0), (10.0, 5.0)], [np.nan, np.nan])
    assert guard_replay.score(_fire_at(tr, 11.0), tr)[:2] == (1, 2)
    assert guard_replay.score(_fire_at(tr), tr)[:2] == (0, 2)


def test_a_missed_interruption_is_nan() -> None:
    tr = _trace([(0.0, 5.0)], [2.5])
    _, _, lat = guard_replay.score(_fire_at(tr), tr)
    assert np.isnan(lat[0])
