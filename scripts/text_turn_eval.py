# ruff: noqa: E501  (wide report lines)
"""Can the words alone tell whether the speaker has finished? (Turnsense, a text end-of-turn model)

Same recordings and cuts as smart_turn_dangling.py, but the model sees only the transcript of
the audio before the cut, as it would from the streaming recogniser:

  finished   the whole transcript of each clip                      (should be judged complete)
  dangling   transcript up to a word that cannot end a request      (should be judged unfinished)
  open       every other word boundary before the end ("play opalite") - ambiguous by nature:
             the speaker may be done or may go on; reported for information only

Text is shown to the model lower case without punctuation (what a streaming recogniser gives) and
as Parakeet writes it. Turnsense: Apache-2.0, SmolLM2-135M, https://huggingface.co/latishab/turnsense

    uv run --with transformers --group laptop python scripts/text_turn_eval.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_tries as st  # noqa: E402
import smart_turn_dangling as sd  # noqa: E402
import smart_turn_eval as se  # noqa: E402


class TextTurn:
    def __init__(self, model_dir: Path, threads: int = 1) -> None:
        from transformers import AutoTokenizer

        self.tok = AutoTokenizer.from_pretrained(str(model_dir))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(
            str(model_dir / "model_quantized.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        self.ms: list[float] = []

    def prob(self, text: str) -> float:
        """P(the speaker has finished), from the words only."""
        enc = self.tok(f"<|user|> {text}", return_tensors="np")
        t0 = time.perf_counter()
        out = self.sess.run(
            None,
            {
                "input_ids": enc["input_ids"].astype(np.int64),
                "attention_mask": enc["attention_mask"].astype(np.int64),
            },
        )[0]
        self.ms.append((time.perf_counter() - t0) * 1000)
        return float(out[0][1])


def plain(words: list[str]) -> str:
    return " ".join(words)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, default=Path("models/turn-detector"))
    ap.add_argument(
        "--dirs", type=Path, nargs="+", default=[Path("bench/mic_hard"), Path("bench/mic_heldout")]
    )
    ap.add_argument("--threshold", type=float, nargs="+", default=[0.5, 0.7, 0.9])
    ap.add_argument("--prose", type=Path, default=Path("out/tries"))
    args = ap.parse_args()

    rec = st.recognizer([], None, False, 4)
    tt = TextTurn(args.model_dir)
    tt.prob("warm up")
    tt.ms.clear()

    sets = {"commands": args.dirs, "read prose": [args.prose]}
    for label, dirs in sets.items():
        fin_lc, fin_raw, dang, other = [], [], [], []
        for _name, pcm in se.load_clips(dirs):
            words = sd.words_with_times(rec, pcm)
            if not words:
                continue
            toks = [w for w, _, _ in words]
            fin_lc.append(tt.prob(plain(toks)))
            for k, w in enumerate(toks[:-1]):
                (dang if w in sd.DANGLING else other).append(tt.prob(plain(toks[: k + 1])))
        if not fin_lc:
            continue
        # the same finished clips as Parakeet writes them (capitals, punctuation)
        for _name, pcm in se.load_clips(dirs):
            s = rec.create_stream()
            s.accept_waveform(
                16000,
                np.concatenate([np.zeros(8000, np.float32), pcm, np.zeros(16000, np.float32)]),
            )
            rec.decode_stream(s)
            fin_raw.append(tt.prob(s.result.text.strip()))
        print(
            f"\n# {label}: {len(fin_lc)} clips, {len(dang)} dangling cuts, {len(other)} other cuts"
        )
        print(
            "| threshold | finished, as streamed | finished, as Parakeet writes it | dangling wrongly ended | other cuts judged complete |"
        )
        print("|---|---|---|---|---|")
        for thr in args.threshold:

            def pc(v: list[float], thr: float = thr) -> str:
                a = np.array(v)
                return f"{np.mean(a > thr):.0%} ({int(np.sum(a > thr))}/{len(a)})"

            print(f"| {thr} | {pc(fin_lc)} | {pc(fin_raw)} | {pc(dang)} | {pc(other)} |")
        d = np.array(dang)
        print(
            f"dangling scores: median {np.median(d):.2f}, p90 {np.percentile(d, 90):.2f}, max {d.max():.2f}"
        )
    m = np.array(tt.ms)
    print(
        f"\ncost per check (1 thread, unpadded): p50 {np.median(m):.0f} ms, p95 {np.percentile(m, 95):.0f} ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
