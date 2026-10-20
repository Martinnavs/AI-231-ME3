# ruff: noqa: E501  (wide report lines)
"""The text end-of-turn check on the words we really have at a short pause: Kroko's streaming text.

text_turn_eval.py shows the model Parakeet's words, which only exist ~340 ms after the pause starts.
At a 200 ms pause the only transcript available is Kroko's interim text, which lags the speech.
Here each cut (the real end of a clip, or just after a dangling word) is followed by s ms of room
noise, the audio is streamed through Kroko, and its interim text goes to the text model.

    uv run --with transformers --group laptop python scripts/text_turn_kroko.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import score_tries as st  # noqa: E402
import smart_turn_dangling as sd  # noqa: E402
import smart_turn_eval as se  # noqa: E402
from text_turn_eval import TextTurn  # noqa: E402

from lan_speech.engines import SherpaSTTEngine  # noqa: E402

SR = 16000


def kroko_interim(kroko: SherpaSTTEngine, pcm: np.ndarray) -> str:
    stream = kroko.create_stream(SR)
    last = ""
    pcm16 = (np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes()
    for i in range(0, len(pcm16), 3200):
        stream.accept_waveform(SR, pcm16[i : i + 3200])
        res = stream.decode()
        if res is not None and res.text:
            last = res.text
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, default=Path("models/turn-detector"))
    ap.add_argument(
        "--dirs", type=Path, nargs="+", default=[Path("bench/mic_hard"), Path("bench/mic_heldout")]
    )
    ap.add_argument("--silences", type=float, nargs="+", default=[0.2, 0.5])
    ap.add_argument("--threshold", type=float, nargs="+", default=[0.5, 0.7])
    args = ap.parse_args()

    rec = st.recognizer([], None, False, 4)
    kroko = SherpaSTTEngine("models", threads=2, model="kroko")
    tt = TextTurn(args.model_dir)
    rng = np.random.default_rng(0)
    fin: dict[float, list[float]] = {s: [] for s in args.silences}
    dang: dict[float, list[float]] = {s: [] for s in args.silences}
    lag = []  # does Kroko's text at the cut already contain the last word?
    examples: list[tuple[float, str]] = []
    for _name, pcm in se.load_clips(args.dirs):
        words = sd.words_with_times(rec, pcm)
        if not words:
            continue
        _, noise = se.segments(pcm)
        cuts = [("fin", int(words[-1][2] * SR), len(words))]
        for k, (w, _a, end) in enumerate(words[:-1]):
            if w in sd.DANGLING:
                cuts.append(("dang", int(min(end, words[k + 1][1]) * SR), k + 1))
        for kind, point, n_words in cuts:
            for s in args.silences:
                quiet = rng.normal(0, noise, int(s * SR)).astype(np.float32)
                text = kroko_interim(kroko, np.concatenate([pcm[:point], quiet]))
                p = tt.prob(text.lower()) if text.strip() else 0.0
                (fin if kind == "fin" else dang)[s].append(p)
                if kind == "fin" and s == args.silences[0]:
                    said = " ".join(w for w, _, _ in words[:n_words])
                    lag.append(len(text.split()) / max(len(said.split()), 1))
                    if len(examples) < 6:
                        examples.append((p, f"kroko {text!r} | said {said!r}"))

    print(
        f"{len(fin[args.silences[0]])} clips, {len(dang[args.silences[0]])} dangling cuts; "
        f"Kroko had {np.mean(lag):.0%} of the words at the end of finished clips (mean, {args.silences[0] * 1000:.0f} ms pause)"
    )
    for p, e in examples:
        print(f"  p={p:.2f}  {e}")
    print("\n| silence | threshold | finished turns ended | dangling wrongly ended |")
    print("|---|---|---|---|")
    for s in args.silences:
        for thr in args.threshold:
            f, d = np.array(fin[s]), np.array(dang[s])
            print(
                f"| {s * 1000:.0f} ms | {thr} | {np.mean(f > thr):.0%} ({int(np.sum(f > thr))}/{len(f)}) | {np.mean(d > thr):.0%} ({int(np.sum(d > thr))}/{len(d)}) |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
