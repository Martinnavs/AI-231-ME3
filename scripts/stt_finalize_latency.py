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
    ap.add_argument(
        "--hold",
        type=float,
        default=0.5,
        help="seconds of the end of each turn fed in real time, like the client's pause hold",
    )
    ap.add_argument(
        "--early", type=float, nargs="*", default=[], help="also time these early-decode pauses (s)"
    )
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

    def measure(early: float | None) -> list[tuple[float, float, float, float]]:
        engine._early = early  # type: ignore[attr-defined]
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
                n_chunks = (len(pcm) + 3199) // 3200
                paced_from = n_chunks - int(args.hold / 0.1)
                for k, i in enumerate(range(0, len(pcm), 3200)):  # 100 ms chunks
                    stream.accept_waveform(16000, pcm[i : i + 3200])
                    res = stream.decode()
                    ended = ended or (res is not None and res.is_final)
                    if k >= paced_from:
                        time.sleep(
                            0.1
                        )  # the pause hold happens in real time, so a background decode can overlap it
                if ended:
                    continue
                t0 = time.perf_counter()
                stream.finalize_result()
                total = (time.perf_counter() - t0) * 1000
                if rnd > 0 and "parakeet" in inner:
                    rows.append((dur, total, inner["parakeet"], inner["audio_s"]))
        return rows

    modes: list[float | None] = [None, *args.early]
    results = {m: measure(m) for m in modes}
    for mode in modes:
        rows = results[mode]
        tot = np.array([r[1] for r in rows])
        dur = np.array([r[0] for r in rows])
        label = "no early decode" if mode is None else f"early decode after a {mode} s pause"
        print(
            f"\n### {label}: {len(rows)} finalizes, last {args.hold} s of each turn fed in real time"
        )
        print("| after `finalize` is sent | p50 | p95 | max |")
        print("|---|---|---|---|")
        print(
            f"| time until the final transcript | {np.percentile(tot, 50):.0f} ms | {np.percentile(tot, 95):.0f} ms | {tot.max():.0f} ms |"
        )
        for lo, hi in ((0, 3), (3, 5), (5, 8), (8, 99)):
            m = (dur >= lo) & (dur < hi)
            if m.any():
                print(
                    f"|   turns {lo}-{hi if hi < 99 else '+'} s ({m.sum()}) | {np.median(tot[m]):.0f} ms | | {tot[m].max():.0f} ms |"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
