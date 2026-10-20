# ruff: noqa: E501  (wide table output lines)
"""Parakeet speed test for the phone (Termux) or any aarch64 box. Standalone: numpy + sherpa-onnx.

Decodes the synthetic clips over and over for a few minutes and prints, per round, the decode
time and real-time factor plus the temperatures, so thermal throttling shows up as a trend.
Synthetic speech is for speed only; this says nothing about accuracy.

Setup in Termux (details in docs/STT-HOSTING.md):
    pkg install python python-numpy
    pip install sherpa-onnx
    # copy to the phone: the Parakeet folder (4 files) and the synthetic clips (out/synth)
    python phone_bench.py --model ~/parakeet-v2-int8 --clips ~/synth --threads 4 --minutes 10

On the laptop it takes the same arguments, which makes the comparison direct.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

SR = 16000


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def temps() -> str:
    out = []
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp"))[:12]:
        try:
            v = int(Path(zone).read_text()) / 1000
            if 10 < v < 120:
                out.append(f"{v:.0f}")
        except (OSError, ValueError):
            pass
    try:  # Termux:API, if installed
        b = json.loads(
            subprocess.run(
                ["termux-battery-status"], capture_output=True, text=True, timeout=5
            ).stdout
        )
        out.append(f"batt {b.get('temperature')}C {b.get('percentage')}%")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return " ".join(out) or "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--clips", type=Path, required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--round-clips", type=int, default=30, help="clips per reported round")
    args = ap.parse_args()

    rec = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(args.model / "encoder.int8.onnx"),
        decoder=str(args.model / "decoder.int8.onnx"),
        joiner=str(args.model / "joiner.int8.onnx"),
        tokens=str(args.model / "tokens.txt"),
        num_threads=args.threads,
        sample_rate=SR,
        feature_dim=80,
        model_type="nemo_transducer",
        decoding_method="greedy_search",
    )
    clips = [
        np.concatenate([np.zeros(8000, np.float32), read_wav(p), np.zeros(24000, np.float32)])
        for p in sorted(args.clips.glob("*.wav"))
    ]
    print(
        f"{len(clips)} clips, {args.threads} threads, cores {os.cpu_count()}, start temps: {temps()}"
    )
    st = rec.create_stream()
    st.accept_waveform(SR, clips[0])
    rec.decode_stream(st)  # warm-up
    t_end = time.time() + args.minutes * 60
    rnd = 0
    base: float | None = None
    while time.time() < t_end:
        ms: list[float] = []
        audio = 0.0
        for i in range(args.round_clips):
            x = clips[(rnd * args.round_clips + i) % len(clips)]
            t0 = time.perf_counter()
            s = rec.create_stream()
            s.accept_waveform(SR, x)
            rec.decode_stream(s)
            ms.append((time.perf_counter() - t0) * 1000)
            audio += len(x) / SR
        rnd += 1
        p50 = float(np.percentile(ms, 50))
        base = base or p50
        print(
            f"round {rnd:3d}  t+{(time.time() - (t_end - args.minutes * 60)) / 60:4.1f} min  "
            f"p50 {p50:4.0f} ms  p95 {np.percentile(ms, 95):4.0f} ms  RTF {sum(ms) / 1000 / audio:.3f}  "
            f"vs first {p50 / base:.2f}x  temps {temps()}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
