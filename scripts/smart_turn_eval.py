# ruff: noqa: E501  (wide report lines)
"""Would Pipecat's Smart Turn v3 end our turns sooner without cutting people off?

Smart Turn scores the audio so far: how likely is it that the speaker has finished? We compare it
with the fixed pause we use today (0.5-0.6 s of quiet) on recorded clips.

For every clip we find where the speech really ends and the pauses inside it (quiet runs of at
least --min-gap seconds between stretches of speech, measured on the recording's own noise floor).
Then, for a range of silence lengths s, we cut the clip and ask the model:

  complete   cut = end of the last word + s of quiet        (the model should say "done")
  incomplete cut = start of an inner pause + s of quiet     (the model should say "not yet")
                   only for s no longer than that pause really lasted in the recording

Read the table as: at silence s, what share of finished turns would be ended (earlier than the
fixed pause), and what share of mid-sentence pauses would be wrongly ended (cut-offs).

    uv run --with transformers --group laptop python scripts/smart_turn_eval.py
"""

from __future__ import annotations

import argparse
import time
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort

SR = 16000
FRAME = 1600  # 100 ms
WINDOW_S = 8


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def segments(pcm: np.ndarray, ratio: float = 2.5) -> tuple[list[tuple[int, int]], float]:
    """Stretches of speech as (start_frame, end_frame) in 100 ms frames, and the noise floor.

    Frames above ratio x the 10th-percentile level are speech; a single quiet frame inside a word
    is bridged."""
    n = len(pcm) // FRAME
    lv = np.sqrt(np.mean(pcm[: n * FRAME].reshape(n, FRAME) ** 2, axis=1))
    noise = max(float(np.percentile(lv, 10)), 0.002)
    on = lv > noise * ratio
    for i in range(1, n - 1):
        if not on[i] and on[i - 1] and on[i + 1]:
            on[i] = True
    segs: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            segs.append((i, j))
            i = j
        else:
            i += 1
    return segs, noise


class SmartTurn:
    def __init__(self, model: Path, threads: int) -> None:
        from transformers import WhisperFeatureExtractor

        self.fe = WhisperFeatureExtractor(chunk_length=WINDOW_S)
        opts = ort.SessionOptions()
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = threads
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
        self.feature_ms: list[float] = []
        self.model_ms: list[float] = []

    def prob(self, pcm: np.ndarray) -> float:
        pcm = pcm[-WINDOW_S * SR :]
        t0 = time.perf_counter()
        feats = self.fe(
            pcm,
            sampling_rate=SR,
            return_tensors="np",
            padding="max_length",
            max_length=WINDOW_S * SR,
            truncation=True,
            do_normalize=True,
        ).input_features.astype(np.float32)
        t1 = time.perf_counter()
        out = self.sess.run(None, {"input_features": feats})[0]
        t2 = time.perf_counter()
        self.feature_ms.append((t1 - t0) * 1000)
        self.model_ms.append((t2 - t1) * 1000)
        return float(np.asarray(out).reshape(-1)[0])


def cut(
    pcm: np.ndarray, point: int, s: float, noise: float, rng: np.random.Generator
) -> np.ndarray:
    """Audio up to `point` plus s seconds of what was recorded after it (room noise if it runs out)."""
    end = point + int(s * SR)
    if end <= len(pcm):
        return pcm[:end]
    pad = rng.normal(0, noise, end - len(pcm)).astype(np.float32)
    return np.concatenate([pcm, pad])


def load_clips(dirs: list[Path]) -> list[tuple[str, np.ndarray]]:
    out = []
    for d in dirs:
        for f in sorted(d.glob("*.wav")):
            out.append((f"{d.name}/{f.name}", read_wav(f)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--model", type=Path, default=Path("models/smart-turn/smart-turn-v3.2-cpu.onnx")
    )
    ap.add_argument(
        "--dirs",
        type=Path,
        nargs="+",
        default=[Path("bench/mic_hard"), Path("bench/mic_heldout"), Path("out/tries")],
    )
    ap.add_argument(
        "--silences", type=float, nargs="+", default=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8]
    )
    ap.add_argument("--min-gap", type=float, default=0.2)
    ap.add_argument("--threshold", type=float, nargs="+", default=[0.5, 0.7, 0.9])
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    st = SmartTurn(args.model, args.threads)
    rng = np.random.default_rng(0)
    clips = load_clips(args.dirs)[: args.limit]
    st.prob(clips[0][1])  # warm-up
    st.feature_ms.clear()
    st.model_ms.clear()

    # probs[kind][s] -> list of probabilities
    complete: dict[float, list[float]] = {s: [] for s in args.silences}
    incomplete: dict[float, list[float]] = {s: [] for s in args.silences}
    gaps = []
    n_inner = 0
    for _name, pcm in clips:
        segs, noise = segments(pcm)
        if not segs:
            continue
        end = segs[-1][1] * FRAME
        for s in args.silences:
            complete[s].append(st.prob(cut(pcm, end, s, noise, rng)))
        for (_, e0), (s1, _) in zip(segs, segs[1:], strict=False):
            gap = (s1 - e0) * FRAME / SR
            if gap < args.min_gap:
                continue
            n_inner += 1
            gaps.append(gap)
            for s in args.silences:
                if s <= gap:  # real recorded quiet only
                    incomplete[s].append(st.prob(pcm[: e0 * FRAME + int(s * SR)]))

    print(
        f"{len(clips)} clips, {len(complete[args.silences[0]])} with speech, {n_inner} inner pauses >= {args.min_gap:g} s"
    )
    if gaps:
        print(
            f"inner pause lengths: median {np.median(gaps):.2f} s, p90 {np.percentile(gaps, 90):.2f} s, max {max(gaps):.2f} s"
        )
    for thr in args.threshold:
        print(f"\n## threshold {thr}")
        print(
            "| silence after last word | finished turns ended | mid-sentence pauses wrongly ended | pauses tested |"
        )
        print("|---|---|---|---|")
        for s in args.silences:
            c = np.array(complete[s])
            i = np.array(incomplete[s])
            wrong = f"{np.mean(i > thr):.0%} ({int(np.sum(i > thr))}/{len(i)})" if len(i) else "n/a"
            print(
                f"| {s * 1000:.0f} ms | {np.mean(c > thr):.0%} ({int(np.sum(c > thr))}/{len(c)}) | {wrong} | {len(i)} |"
            )
    f, m = np.array(st.feature_ms), np.array(st.model_ms)
    print(
        f"\ncost per check ({args.threads} thread): features p50 {np.median(f):.0f} ms, model p50 {np.median(m):.0f} ms (p95 {np.percentile(m, 95):.0f}), total p50 {np.median(f + m):.0f} ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
