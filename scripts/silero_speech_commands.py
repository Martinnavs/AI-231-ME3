#!/usr/bin/env python
"""Does Silero respond to a clean one-word command? (Speech Commands v2, laptop only.)

    python scripts/silero_speech_commands.py fetch out/gsc   # streams 2.3 GB, keeps 5 words
    python scripts/silero_speech_commands.py run out/gsc --noise out/gsc/_background_noise_

Per word: peak confidence of the repository's own SileroOnset (fresh state per clip, 1 s padded),
and the share of clips on which each gate would fire. Also repeats at -20 dB, and on 1 s slices of
the dataset's background-noise files (the false-fire check; no assistant echo in it).

``fetch`` needs the network; the dataset is CC BY 4.0 (Warden, arXiv:1804.03209). Nothing here
touches the Pi: the answer is a property of the model and the audio.
"""

import argparse
import json
import random
import sys
import tarfile
import urllib.request
import wave
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from doc_chat.bargein import SileroOnset  # noqa: E402

URL = "http://download.tensorflow.org/data/speech_commands_v0.02.tar.gz"
WORDS = ("stop", "no", "go", "yes", "up")
GATES = {"0.7/250ms (spec)": (0.7, 0.25), "0.5/250ms": (0.5, 0.25), "0.5/100ms": (0.5, 0.10)}


def fetch(dest: Path) -> None:
    n = 0
    with urllib.request.urlopen(URL) as resp, tarfile.open(fileobj=resp, mode="r|gz") as tar:
        for m in tar:
            parts = [p for p in Path(m.name).parts if p != "."]
            word = parts[0] if parts else ""
            noise = word == "_background_noise_"
            if not (m.isfile() and len(parts) == 2 and (word in WORDS or noise)):
                continue
            f = tar.extractfile(m)
            if f is None:
                continue
            (dest / word).mkdir(parents=True, exist_ok=True)
            (dest / word / parts[1]).write_bytes(f.read())
            n += 1
    print(f"extracted {n} files to {dest}")


def load(path: Path) -> np.ndarray[Any, Any] | None:
    with wave.open(str(path)) as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1:
            return None
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def one_clip(model: str, x: np.ndarray) -> dict[str, Any]:
    x = np.concatenate((x, np.zeros(max(0, 16000 - len(x)), dtype=np.int16)))
    probe = SileroOnset(model, keep_history=True)
    probe.feed(x.tobytes())
    peak = max(probe.history or [0.0])
    out: dict[str, Any] = {"peak": float(peak)}
    for name, (conf, min_s) in GATES.items():
        out[name] = bool(SileroOnset(model, confidence=conf, min_s=min_s).feed(x.tobytes()))
    return out


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    peaks = np.array([r["peak"] for r in rows])
    s = {
        "n": len(rows),
        "peak_median": round(float(np.median(peaks)), 2),
        "peak_p10": round(float(np.percentile(peaks, 10)), 2),
        "peak_below_0.2": round(float((peaks < 0.2).mean()), 2),
    }
    for g in GATES:
        s[g] = round(sum(r[g] for r in rows) / len(rows), 2)
    return s


def run(root: Path, model: str, n: int, noise_dir: Path | None) -> dict[str, Any]:
    rng = random.Random(0)
    results: dict[str, Any] = {}
    for word in WORDS:
        files = sorted((root / word).glob("*.wav"))
        rng.shuffle(files)
        clips = [c for c in (load(f) for f in files[:n]) if c is not None]
        results[word] = summarise([one_clip(model, c) for c in clips])
        quiet = [(c.astype(np.float32) * 0.1).astype(np.int16) for c in clips]  # -20 dB
        results[f"{word} (-20 dB)"] = summarise([one_clip(model, c) for c in quiet])
    if noise_dir is not None:
        slices = []
        for f in sorted(noise_dir.glob("*.wav")):
            x = load(f)
            if x is not None:
                slices += [x[i : i + 16000] for i in range(0, len(x) - 16000, 16000)]
        results["background noise (1 s slices)"] = summarise([one_clip(model, c) for c in slices])
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch").add_argument("dest", type=Path)
    r = sub.add_parser("run")
    r.add_argument("root", type=Path)
    r.add_argument("--model", default="assets/silero_vad.onnx")
    r.add_argument("--n", type=int, default=300, help="clips per word")
    r.add_argument("--noise", type=Path)
    args = ap.parse_args()
    if args.cmd == "fetch":
        fetch(args.dest)
        return 0
    res = run(args.root, args.model, args.n, args.noise)
    head = f"{'set':32}{'n':>5}{'peak med':>9}{'<0.2':>6}" + "".join(f"{g:>18}" for g in GATES)
    print(head)
    for k, v in res.items():
        row = f"{k:32}{v['n']:>5}{v['peak_median']:>9}{v['peak_below_0.2']:>6}"
        print(row + "".join(f"{v[g]:>18}" for g in GATES))
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
