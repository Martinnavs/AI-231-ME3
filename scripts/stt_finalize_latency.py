# ruff: noqa: E501  (wide report lines)
"""Time what the server does between `finalize` and the final transcript, on saved turns.

Builds the same engine the server builds (Kroko + Parakeet with hotwords + repair), streams each
saved turn into it as the server would, then times `finalize_result()`: Kroko finishing the turn,
Parakeet decoding the padded audio, and the repair step. No network, no client pause, no LLM or TTS.
The delay a user sees is this plus the client's pause hold (and network).

    uv run --group laptop python scripts/stt_finalize_latency.py out/tries
"""

from __future__ import annotations

import argparse
import glob
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lan_speech.hotwords import load_entities  # noqa: E402
from lan_speech.hybrid_stt import ParakeetHybridSTTEngine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--entities", type=Path, default=Path("config/entities.json"))
    ap.add_argument("--kroko-threads", type=int, default=2)
    ap.add_argument("--parakeet-threads", type=int, default=2)
    ap.add_argument("--pad-lead", type=float, default=0.5)
    ap.add_argument("--pad-tail", type=float, default=1.0)
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    engine = ParakeetHybridSTTEngine(
        "models",
        load_entities(args.entities),
        kroko_threads=args.kroko_threads,
        parakeet_threads=args.parakeet_threads,
        pad_lead_s=args.pad_lead,
        pad_tail_s=args.pad_tail,
    )
    turns = []
    for f in sorted(glob.glob(str(args.folder / "turn-*.wav"))):
        with wave.open(f) as w:
            turns.append((w.readframes(w.getnframes()), w.getnframes() / 16000))
    print(
        f"{len(turns)} turns, mean {np.mean([t[1] for t in turns]):.1f} s; Kroko {args.kroko_threads} / Parakeet {args.parakeet_threads} threads, pad {args.pad_lead}/{args.pad_tail} s\n"
    )

    rows = []
    for rnd in range(args.rounds):
        for pcm, dur in turns:
            stream = engine.create_stream(16000)
            inner: dict[str, float] = {}
            real = stream._transcribe  # type: ignore[attr-defined]

            def timed(samples: np.ndarray, sr: int, real=real, inner=inner) -> str:
                t0 = time.perf_counter()
                out = real(samples, sr)
                inner["parakeet"] = (time.perf_counter() - t0) * 1000
                inner["audio_s"] = len(samples) / sr
                return out

            stream._transcribe = timed  # type: ignore[attr-defined]
            ended = False
            for i in range(0, len(pcm), 3200):  # 100 ms chunks, decoded as they arrive
                stream.accept_waveform(16000, pcm[i : i + 3200])
                res = stream.decode()
                ended = ended or (res is not None and res.is_final)
            if ended:  # Kroko's own endpoint fired first: nothing left for a finalize to time
                continue
            t0 = time.perf_counter()
            stream.finalize_result()
            total = (time.perf_counter() - t0) * 1000
            if rnd > 0 and "parakeet" in inner:  # round 0 warms everything up
                rows.append((dur, total, inner["parakeet"], inner["audio_s"]))

    tot = np.array([r[1] for r in rows])
    pk = np.array([r[2] for r in rows])
    kro = tot - pk
    dur = np.array([r[0] for r in rows])
    p = lambda a, q: float(np.percentile(a, q))  # noqa: E731
    print(f"{len(rows)} measured finalizes\n")
    print("| part | p50 | p95 | max |")
    print("|---|---|---|---|")
    for label, a in (
        ("finalize total (what the server adds after the pause)", tot),
        ("  Parakeet decode of the padded audio", pk),
        ("  Kroko finishing the turn + repair", kro),
    ):
        print(f"| {label} | {p(a, 50):.0f} ms | {p(a, 95):.0f} ms | {a.max():.0f} ms |")
    print("\nby turn length (total finalize):")
    for lo, hi in ((0, 3), (3, 5), (5, 8), (8, 99)):
        m = (dur >= lo) & (dur < hi)
        if m.any():
            print(
                f"  {lo}-{hi if hi < 99 else '+'} s: {m.sum():2d} turns, p50 {np.median(tot[m]):.0f} ms, max {tot[m].max():.0f} ms"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
