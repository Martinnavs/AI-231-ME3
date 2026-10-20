# ruff: noqa: E501  (wide table lines)
"""Score Parakeet+hotwords on the labelled clips under different pre-processing of the audio.

The server hands Parakeet the buffered turn after silence trimming; the evaluations that gave the
published WER padded each clip with zeros (0.5 s before, 1.5 s after). This compares them on the
clips whose text is known, so the choice rests on accuracy rather than on how long a decode takes.

    uv run --group laptop python scripts/stt_padding_eval.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lan_speech.hotwords import load_entities, write_hotwords, write_vocab  # noqa: E402
from lan_speech.hybrid_stt import trim_silence  # noqa: E402

SR = 16000
MODEL = Path("models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
SETS = {
    "hard": (Path("bench/stt_hard.json"), Path("bench/mic_hard")),
    "heldout": (Path("bench/stt_heldout.json"), Path("bench/mic_heldout")),
}
# name -> (trim silence first?, zero lead s, zero tail s)
VARIANTS: dict[str, tuple[bool, float, float]] = {
    "as evaluated so far: no trim, 0.5 + 1.5 s zeros": (False, 0.5, 1.5),
    "server today: trim 0.3/0.4, no zeros": (True, 0.0, 0.0),
    "trim, 0.3 + 0.5 s zeros": (True, 0.3, 0.5),
    "trim, 0.5 + 0.5 s zeros": (True, 0.5, 0.5),
    "trim, 0.5 + 1.0 s zeros": (True, 0.5, 1.0),
    "trim, 0.5 + 1.5 s zeros": (True, 0.5, 1.5),
    "no trim, no zeros (raw clip)": (False, 0.0, 0.0),
}


def load_compare():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("stt_compare", HERE / "stt_compare.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("stt_compare", mod)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    import time

    import sherpa_onnx

    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--names", type=Path, default=Path("config/entities.json"))
    args = ap.parse_args()
    sc = load_compare()
    names = [e.name for e in load_entities(args.names)]
    tmp = Path(tempfile.mkdtemp(prefix="padeval_"))
    write_vocab(MODEL / "tokens.txt", tmp / "v")
    write_hotwords(names, 1.0, tmp / "h")
    rec = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(MODEL / "encoder.int8.onnx"),
        decoder=str(MODEL / "decoder.int8.onnx"),
        joiner=str(MODEL / "joiner.int8.onnx"),
        tokens=str(MODEL / "tokens.txt"),
        num_threads=args.threads,
        sample_rate=SR,
        feature_dim=80,
        model_type="nemo_transducer",
        decoding_method="modified_beam_search",
        max_active_paths=4,
        hotwords_file=str(tmp / "h"),
        hotwords_score=1.0,
        modeling_unit="bpe",
        bpe_vocab=str(tmp / "v"),
    )
    clips = []
    for set_name, (items_path, audio_dir) in SETS.items():
        for it in json.loads(items_path.read_text()):
            if it["kind"] == "noise" or not it["text"]:
                continue
            pcm = sc.read_wav(audio_dir / f"{it['id']}.wav").astype(np.float32) / 32768
            clips.append((set_name, it, pcm))
    print(f"{len(clips)} labelled clips (hard + held-out), hotwords on\n")
    print(
        "| variant | WER hard | WER held-out | entities hard | entities held-out | decode p50 ms |"
    )
    print("|---|---|---|---|---|---|")
    for label, (trim, lead, tail) in VARIANTS.items():
        agg = {s: [0, 0, 0, 0] for s in SETS}  # edits, words, ent hit, ent total
        ms = []
        for set_name, it, pcm in clips:
            x = trim_silence(pcm, SR, 0.3, 0.4) if trim else pcm
            x = np.concatenate(
                [np.zeros(int(lead * SR), np.float32), x, np.zeros(int(tail * SR), np.float32)]
            )
            t0 = time.perf_counter()
            st = rec.create_stream()
            st.accept_waveform(SR, x)
            rec.decode_stream(st)
            ms.append((time.perf_counter() - t0) * 1000)
            hyp = sc.norm_words(st.result.text)
            ref = sc.norm_words(it["text"])
            a = agg[set_name]
            a[0] += sc.word_edits(ref, hyp)
            a[1] += len(ref)
            text = " " + " ".join(hyp) + " "
            for e in it["entities"]:
                a[3] += 1
                a[2] += f" {' '.join(sc.norm_words(e))} " in text
        row = [f"{agg[s][0] / agg[s][1]:.1%}" for s in SETS] + [
            f"{agg[s][2]}/{agg[s][3]}" for s in SETS
        ]
        print(f"| {label} | {' | '.join(row)} | {np.median(ms):.0f} |", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
