# ruff: noqa: E501  (wide report lines)
"""Score turns saved by `try_stt.py --save DIR` against what was actually said.

DIR/truth.tsv has one line per turn: the turn number, a tab, the words spoken. Reports the WER
of what the live server returned, then re-decodes the saved audio under other settings (hotword
score, padding, no hotwords) so a setting can be chosen on this room and this voice.

    uv run --group laptop python scripts/score_tries.py out/tries
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lan_speech.hotwords import load_entities, write_hotwords, write_vocab  # noqa: E402
from lan_speech.hybrid_stt import pad_with_zeros, trim_silence  # noqa: E402

SR = 16000
MODEL = Path("models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")


def load_compare() -> Any:
    spec = importlib.util.spec_from_file_location("stt_compare", HERE / "stt_compare.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("stt_compare", mod)
    spec.loader.exec_module(mod)
    return mod


def recognizer(names: list[str], score: float | None, beam: bool, threads: int) -> Any:
    import sherpa_onnx

    base: dict[str, Any] = dict(
        encoder=str(MODEL / "encoder.int8.onnx"),
        decoder=str(MODEL / "decoder.int8.onnx"),
        joiner=str(MODEL / "joiner.int8.onnx"),
        tokens=str(MODEL / "tokens.txt"),
        num_threads=threads,
        sample_rate=SR,
        feature_dim=80,
        model_type="nemo_transducer",
    )
    if score is not None:
        tmp = Path(tempfile.mkdtemp(prefix="score_tries_"))
        write_vocab(MODEL / "tokens.txt", tmp / "v")
        write_hotwords(names, score, tmp / "h")
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            decoding_method="modified_beam_search",
            max_active_paths=4,
            hotwords_file=str(tmp / "h"),
            hotwords_score=score,
            modeling_unit="bpe",
            bpe_vocab=str(tmp / "v"),
            **base,
        )
    method = "modified_beam_search" if beam else "greedy_search"
    return sherpa_onnx.OfflineRecognizer.from_transducer(decoding_method=method, **base)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--names", type=Path, default=Path("config/entities.json"))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--show", action="store_true", help="print every turn for every setting")
    args = ap.parse_args()
    sc = load_compare()
    truth = {}
    for line in (args.folder / "truth.tsv").read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            n, text = line.split("\t", 1)
            truth[int(n)] = text.strip()
    turns = {}
    for j in sorted(glob.glob(str(args.folder / "turn-*.json"))):
        n = int(Path(j).stem.rsplit("-", 1)[1])
        if n in truth:
            with wave.open(j[:-5] + ".wav") as w:
                pcm = (
                    np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
                )
            turns[n] = (pcm, json.loads(Path(j).read_text(encoding="utf-8")))
    names = [e.name for e in load_entities(args.names)]

    def wer(hyps: dict[int, str]) -> tuple[float, int, int]:
        e = w = 0
        for n, h in hyps.items():
            ref = sc.norm_words(truth[n])
            e += sc.word_edits(ref, sc.norm_words(h))
            w += len(ref)
        return e / w, e, w

    print(
        f"{len(turns)} scored turns, {sum(len(sc.norm_words(truth[n])) for n in turns)} reference words\n"
    )
    live = {n: d["text"] for n, (_, d) in turns.items()}
    raw = {n: d.get("raw_text") or d["text"] for n, (_, d) in turns.items()}
    print("| setting | WER | word errors |")
    print("|---|---|---|")
    for label, h in (
        ("live server output (after name repair)", live),
        ("live server, Parakeet raw (before repair)", raw),
    ):
        r = wer(h)
        print(f"| {label} | {r[0]:.1%} | {r[1]} of {r[2]} |")

    configs: dict[str, tuple[Any, tuple[float, float]]] = {}
    for label, score, beam in (
        ("hotwords 1.0", 1.0, True),
        ("hotwords 0.5", 0.5, True),
        ("no hotwords, beam 4", None, True),
        ("no hotwords, greedy", None, False),
    ):
        configs[label] = (recognizer(names, score, beam, args.threads), (0.5, 1.0))
    variants: list[tuple[str, str, tuple[float, float] | None]] = [
        ("hotwords 1.0", "hotwords 1.0, pad 0.5/1.0 (server default)", (0.5, 1.0)),
        ("hotwords 1.0", "hotwords 1.0, no padding", (0.0, 0.0)),
        ("hotwords 1.0", "hotwords 1.0, pad 0.5/1.5", (0.5, 1.5)),
        ("hotwords 0.5", "hotwords 0.5, pad 0.5/1.0", (0.5, 1.0)),
        ("no hotwords, beam 4", "no hotwords (beam 4), pad 0.5/1.0", (0.5, 1.0)),
        ("no hotwords, greedy", "no hotwords (greedy), pad 0.5/1.0", (0.5, 1.0)),
    ]
    out: dict[str, dict[int, str]] = {}
    for cfg, label, pads in variants:
        rec = configs[cfg][0]
        hyps = {}
        for n, (pcm, _) in turns.items():
            x = trim_silence(pcm, SR, 0.3, 0.4)
            assert pads is not None
            x = pad_with_zeros(x, SR, *pads)
            st = rec.create_stream()
            st.accept_waveform(SR, x)
            rec.decode_stream(st)
            hyps[n] = str(st.result.text).strip()
        out[label] = hyps
        r = wer(hyps)
        print(f"| {label} | {r[0]:.1%} | {r[1]} of {r[2]} |", flush=True)

    base = out["hotwords 1.0, pad 0.5/1.0 (server default)"]
    print("\n## Per turn: server default vs what was said (turns with any error)")
    for n in sorted(turns):
        ref = sc.norm_words(truth[n])
        e = sc.word_edits(ref, sc.norm_words(base[n]))
        if e or args.show:
            print(f"{n:02d} errors {e}\n   said:  {truth[n]}\n   heard: {base[n]}")
            for lab in ("hotwords 0.5, pad 0.5/1.0", "no hotwords (beam 4), pad 0.5/1.0"):
                e2 = sc.word_edits(ref, sc.norm_words(out[lab][n]))
                if out[lab][n] != base[n]:
                    print(f"   {lab} ({e2} errors): {out[lab][n]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
