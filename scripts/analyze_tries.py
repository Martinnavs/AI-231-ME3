# ruff: noqa: E501  (wide report lines)
"""Analyse turns saved by `try_stt.py --save DIR`, without needing a transcript of what was said.

* edges    does trimming the silence (what the server does) change the first or last words,
           compared with decoding the whole saved buffer with generous padding?
* hotwords how often, and where, do hotwords change the words compared with plain decoding?
* levels   noise floor, speech level and clipping of the recordings
* cuts     turns that look cut short by a pause (no closing punctuation, very short)

    uv run --group laptop python scripts/analyze_tries.py out/tries
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lan_speech.hotwords import load_entities, write_hotwords, write_vocab  # noqa: E402
from lan_speech.hybrid_stt import trim_silence  # noqa: E402

SR = 16000
MODEL = Path("models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")


def words(text: str) -> list[str]:
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text.lower().replace("'", "").replace("’", ""))
    return re.sub(r"[^a-z0-9]+", " ", t).split()


def recognizers(names: list[str], threads: int) -> dict[str, Any]:
    import sherpa_onnx

    tmp = Path(tempfile.mkdtemp(prefix="tries_"))
    write_vocab(MODEL / "tokens.txt", tmp / "v")
    write_hotwords(names, 1.0, tmp / "h")
    base = dict(
        encoder=str(MODEL / "encoder.int8.onnx"),
        decoder=str(MODEL / "decoder.int8.onnx"),
        joiner=str(MODEL / "joiner.int8.onnx"),
        tokens=str(MODEL / "tokens.txt"),
        num_threads=threads,
        sample_rate=SR,
        feature_dim=80,
        model_type="nemo_transducer",
    )
    return {
        "greedy": sherpa_onnx.OfflineRecognizer.from_transducer(
            decoding_method="greedy_search", **base
        ),
        "hot": sherpa_onnx.OfflineRecognizer.from_transducer(
            decoding_method="modified_beam_search",
            max_active_paths=4,
            hotwords_file=str(tmp / "h"),
            hotwords_score=1.0,
            modeling_unit="bpe",
            bpe_vocab=str(tmp / "v"),
            **base,
        ),
    }


def decode(rec: Any, x: np.ndarray) -> tuple[str, float]:
    t0 = time.perf_counter()
    s = rec.create_stream()
    s.accept_waveform(SR, x)
    rec.decode_stream(s)
    return str(s.result.text).strip(), (time.perf_counter() - t0) * 1000


def pad(x: np.ndarray, lead: float, tail: float) -> np.ndarray:
    return np.concatenate(
        [np.zeros(int(lead * SR), np.float32), x, np.zeros(int(tail * SR), np.float32)]
    )


def edge_diff(ref: list[str], got: list[str]) -> str:
    """Name what changed at the edges: words lost or added at the start or the end."""
    if ref == got:
        return ""
    i = 0
    while i < min(len(ref), len(got)) and ref[i] == got[i]:
        i += 1
    j = 0
    while j < min(len(ref), len(got)) - i and ref[-1 - j] == got[-1 - j]:
        j += 1
    ref_mid, got_mid = ref[i : len(ref) - j], got[i : len(got) - j]
    where = (
        "start"
        if i == 0 and j > 0
        else "end"
        if j == 0 and i > 0
        else "middle"
        if i and j
        else "everywhere"
    )
    return f"{where}: '{' '.join(ref_mid)}' -> '{' '.join(got_mid)}'"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--names", type=Path, default=Path("config/entities.json"))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--only", nargs="+", choices=["edges", "hotwords", "levels", "cuts"])
    args = ap.parse_args()
    todo = args.only or ["levels", "cuts", "edges", "hotwords"]

    turns = []
    for j in sorted(glob.glob(str(args.folder / "turn-*.json"))):
        with wave.open(j[:-5] + ".wav") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        turns.append((Path(j).stem.split("-")[-1], pcm, json.loads(Path(j).read_text())))
    print(f"{len(turns)} turns, {sum(len(t[1]) for t in turns) / SR / 60:.1f} min of audio\n")

    if "levels" in todo:
        print("## Recording levels")
        noise, speech, clipped = [], [], 0
        for _, x, _ in turns:
            fr = np.sqrt(np.mean(x[: len(x) // 320 * 320].reshape(-1, 320) ** 2, axis=1))
            noise.append(np.percentile(fr, 10))
            speech.append(np.percentile(fr, 95))
            clipped += int(np.mean(np.abs(x) > 0.99) > 0.001)
        db = lambda v: 20 * np.log10(max(float(v), 1e-6))  # noqa: E731
        print(
            f"noise floor (10th percentile of 20 ms frames): median {db(np.median(noise)):.0f} dBFS, worst {db(max(noise)):.0f}"
        )
        print(
            f"speech level (95th percentile): median {db(np.median(speech)):.0f} dBFS, quietest {db(min(speech)):.0f}"
        )
        print(
            f"signal-to-noise (median): {db(np.median(speech)) - db(np.median(noise)):.0f} dB; turns with clipping: {clipped}\n"
        )

    if "cuts" in todo:
        print("## Turns that look cut short by a pause")
        sus = []
        for n, x, d in turns:
            t = d["text"].strip()
            ends_open = not re.search(r"[.?!]['\"]?$", t) or re.search(
                r"\b(the|a|an|of|to|and|is|was|that|this|in|on|at|for|with|his|her)[.,]?$", t, re.I
            )
            if ends_open or t[:1].islower() or len(words(t)) <= 3:
                sus.append((n, len(x) / SR, t))
        print(
            f"{len(sus)} of {len(turns)} turns end open, start in lower case, or have 3 words or fewer:"
        )
        for n, dur, t in sus:
            print(f"  {n} {dur:4.1f}s {t!r}")
        print()

    if not ({"edges", "hotwords"} & set(todo)):
        return 0
    names = [e.name for e in load_entities(args.names)]
    recs = recognizers(names, args.threads)
    hot = recs["hot"]

    if "edges" in todo:
        print(
            "## Does trimming clip the first or last word? (hotword decode; reference = whole buffer, 1.0 s lead + 1.5 s tail)"
        )
        variants = {
            "server (0.3 lead / 0.4 tail)": (0.3, 0.4),
            "tight (0.1 / 0.1)": (0.1, 0.1),
            "none (0 / 0)": (0.0, 0.0),
        }
        decode(hot, pad(turns[0][1], 1, 1.5))
        ref = {n: decode(hot, pad(x, 1.0, 1.5)) for n, x, _ in turns}
        full_ms = []
        for label, (lead, tail) in variants.items():
            diffs, ms = [], []
            for n, x, _ in turns:
                trimmed = trim_silence(x, SR, lead, tail)
                text, t = decode(
                    hot, pad(trimmed, 0.0, 0.0) if lead == 0 and tail == 0 else trimmed
                )
                ms.append(t)
                d = edge_diff(words(ref[n][0]), words(text))
                if d:
                    diffs.append((n, d))
            print(
                f"\n{label}: {len(diffs)} of {len(turns)} turns differ from the reference; decode p50 {np.median(ms):.0f} ms"
            )
            for n, d in diffs:
                print(f"  {n} {d}")
        for _n, x, _ in turns:
            full_ms.append(decode(hot, pad(x, 1.0, 1.5))[1])
        print(
            f"\nreference decode p50 {np.median(full_ms):.0f} ms (whole buffer + padding); mean turn {np.mean([len(t[1]) for t in turns]) / SR:.1f} s\n"
        )

    if "hotwords" in todo:
        print("## Hotwords vs plain decoding on your speech (same audio, server trimming)")
        greedy = recs["greedy"]
        listed = {" ".join(words(n)) for n in names}
        changed = intro = 0
        for n, x, _ in turns:
            xt = trim_silence(x, SR, 0.3, 0.4)
            g, _ = decode(greedy, xt)
            h, _ = decode(hot, xt)
            if words(g) != words(h):
                changed += 1
                gw, hw = " " + " ".join(words(g)) + " ", " " + " ".join(words(h)) + " "
                new = sorted(e for e in listed if f" {e} " in hw and f" {e} " not in gw)
                if new:
                    intro += 1
                print(
                    f"  {n}\n    plain:    {g}\n    hotwords: {h}"
                    + (f"\n    listed names that appeared only with hotwords: {new}" if new else "")
                )
        print(
            f"\n{changed} of {len(turns)} turns changed by hotwords; {intro} gained a listed name\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
