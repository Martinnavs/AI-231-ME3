"""Free offline replay of the loudness gate (doc_chat.voice_gate) over the fdbench clips.

Which false-turn-taking clips it blocks, and how many addressed user clips it would wrongly drop,
(a) with only the room calibration (out/calibration.json) as the reference and (b) with the
reference learned over 30 s of answered user turns.

    .venv/bin/python scripts/voice_gate_replay.py [--gap 10]
"""

from __future__ import annotations

import argparse
import glob
import random
import statistics
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from doc_chat.calibrate import load_calibration  # noqa: E402
from doc_chat.voice_gate import LoudnessGate, active_levels  # noqa: E402

DATA = ROOT / "fd-benchmark" / "datasets"


def pcm_of(path: str) -> bytes:
    x, sr = sf.read(path, dtype="float32")
    x = x.mean(1) if x.ndim > 1 else x
    if sr != 16000:
        x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * 16000 / sr)), np.arange(len(x)), x)
    return bytes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap", type=float, default=10.0)
    args = parser.parse_args()
    cal = load_calibration()
    seed = None if cal is None else cal["user_db"]
    user = {
        g: sorted(glob.glob(str(DATA / g / "*.flac")))
        for g in ("v1_turntaking", "v1_pause_synth", "v1_interrupt")
    }
    false = {
        k: sorted(glob.glob(str(DATA / "false_tt" / f"false_{k}_*.flac")))
        for k in ("side_talk", "tv_radio", "background")
    }
    pcms = {p: pcm_of(p) for fs in [*user.values(), *false.values()] for p in fs}
    print(f"calibration level (seed): {seed}   gap {args.gap} dB")
    for name, files in [*false.items(), *user.items()]:
        levels = [active_levels(pcms[f])[1] for f in files]
        levels = [lv for lv in levels if lv is not None]
        print(
            f"  {name:<16} n={len(levels):>3} level median {statistics.median(levels):6.1f} "
            f"min {min(levels):6.1f} max {max(levels):6.1f} dBFS"
        )
    all_user = [f for fs in user.values() for f in fs]
    for label, learned in (
        ("(a) calibration level only", False),
        ("(b) 30 s window learned", True),
    ):
        blocked = {k: 0 for k in false}
        dropped = 0
        for trial in range(20):
            rng = random.Random(trial)
            gate = LoudnessGate(args.gap, calibration_db=seed)
            if learned:  # answered user turns up to 30 s of speech, in a random order
                for f in rng.sample(all_user, len(all_user)):
                    if gate.reference()[1] == "window" and gate._blocks >= gate._max_blocks:
                        break
                    gate.accept(pcms[f])
            for k, files in false.items():
                blocked[k] += sum(gate.check(pcms[f]).far for f in files)
            dropped += sum(gate.check(pcms[f]).far for f in all_user)
        ref = gate.reference()
        print(f"\n{label} (20 random windows; last reference {ref[0]:.1f} dBFS from {ref[1]})")
        for k, files in false.items():
            print(f"  {k:<12} blocked {blocked[k] / 20:.1f} of {len(files)} on average")
        print(f"  addressed user clips wrongly dropped: {dropped / 20:.1f} of {len(all_user)}")


if __name__ == "__main__":
    main()
