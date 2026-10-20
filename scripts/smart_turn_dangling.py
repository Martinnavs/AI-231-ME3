# ruff: noqa: E501  (wide report lines)
"""Does Smart Turn wait when a sentence is clearly not finished?

Real recordings are cut after a word that cannot end a request ("play something by", "set a timer
for", "call my", "and") using Parakeet's word times, then s milliseconds of room noise are
appended: the speaker hesitates there. A fixed pause would end these turns once s reaches its
hold (0.5-0.6 s); a good turn-end model should keep waiting. The full clips (real end of the
sentence) are the control: those the model should end.

    uv run --with transformers --group laptop python scripts/smart_turn_dangling.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_tries as st  # noqa: E402
import smart_turn_eval as se  # noqa: E402

SR = 16000
# words after which no request can be finished
DANGLING = {
    "the", "a", "an", "of", "by", "to", "in", "on", "for", "and", "or", "but", "with", "at",
    "from", "that", "my", "your", "his", "her", "our", "their", "some", "is", "are", "was",
    "were", "be", "about", "into", "than", "as", "if", "so", "because", "then", "which", "who",
    "me", "us", "this", "these", "those", "any", "its", "near", "like", "up",
}  # fmt: skip


def words_with_times(rec: object, pcm: np.ndarray) -> list[tuple[str, float, float]]:
    """(word, start_s, end_s) from the token times; a word ends where its last token starts + 0.12."""
    s = rec.create_stream()  # type: ignore[attr-defined]
    s.accept_waveform(
        SR, np.concatenate([np.zeros(8000, np.float32), pcm, np.zeros(16000, np.float32)])
    )
    rec.decode_stream(s)  # type: ignore[attr-defined]
    toks, ts = list(s.result.tokens), [t - 0.5 for t in s.result.timestamps]
    out: list[list[Any]] = []
    for tok, t in zip(toks, ts, strict=False):
        if tok.startswith(" ") or not out:
            out.append([tok.strip(), t, t])
        else:
            out[-1][0] = str(out[-1][0]) + tok
            out[-1][2] = t
    return [(str(w).lower().strip(".,?!;:"), float(a), float(b) + 0.12) for w, a, b in out]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--model", type=Path, default=Path("models/smart-turn/smart-turn-v3.2-cpu.onnx")
    )
    ap.add_argument(
        "--dirs", type=Path, nargs="+", default=[Path("bench/mic_hard"), Path("bench/mic_heldout")]
    )
    ap.add_argument("--silences", type=float, nargs="+", default=[0.2, 0.3, 0.5, 0.8])
    ap.add_argument("--threshold", type=float, nargs="+", default=[0.5, 0.8, 0.9])
    args = ap.parse_args()

    rec = st.recognizer([], None, False, 4)
    turn = se.SmartTurn(args.model, 1)
    rng = np.random.default_rng(0)
    dang: dict[float, list[float]] = {s: [] for s in args.silences}
    full: dict[float, list[float]] = {s: [] for s in args.silences}
    for _name, pcm in se.load_clips(args.dirs):
        words = words_with_times(rec, pcm)
        _, noise = se.segments(pcm)
        for k, (w, _a, end) in enumerate(words[:-1]):
            if w not in DANGLING:
                continue
            nxt = words[k + 1][1]
            point = int(min(end, nxt) * SR)
            for s in args.silences:
                quiet = rng.normal(0, noise, int(s * SR)).astype(np.float32)
                dang[s].append(turn.prob(np.concatenate([pcm[:point], quiet])))
        if words:
            point = int(words[-1][2] * SR)
            for s in args.silences:
                quiet = rng.normal(0, noise, int(s * SR)).astype(np.float32)
                full[s].append(turn.prob(np.concatenate([pcm[:point], quiet])))

    n = len(dang[args.silences[0]])
    print(f"{len(full[args.silences[0]])} clips, {n} cuts after a dangling word")
    for thr in args.threshold:
        print(f"\n## threshold {thr}")
        print(
            "| silence | real ends recognised (want high) | dangling cuts wrongly ended (want low) |"
        )
        print("|---|---|---|")
        for s in args.silences:
            f, d = np.array(full[s]), np.array(dang[s])
            print(
                f"| {s * 1000:.0f} ms | {np.mean(f > thr):.0%} ({int(np.sum(f > thr))}/{len(f)}) | {np.mean(d > thr):.0%} ({int(np.sum(d > thr))}/{len(d)}) |"
            )
    allp = np.array(dang[0.5 if 0.5 in dang else args.silences[0]])
    print(
        f"\nscore on dangling cuts at 500 ms: median {np.median(allp):.2f}, p10 {np.percentile(allp, 10):.2f}, p90 {np.percentile(allp, 90):.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
