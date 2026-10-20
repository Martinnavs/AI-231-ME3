"""Render the clip scripts to 16 kHz WAVs with Kokoro, for speed benchmarks that need audio.

Synthetic speech is clean and uniform: use these for latency and throughput, never for accuracy.

    uv run --group laptop python scripts/make_synth_clips.py --out out/synth
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--items",
        type=Path,
        nargs="+",
        default=[Path("bench/stt_hard.json"), Path("bench/stt_heldout.json")],
    )
    ap.add_argument("--out", type=Path, default=Path("out/synth"))
    ap.add_argument("--models", type=Path, default=Path("models/kokoro-v1.0"))
    ap.add_argument("--voices", nargs="+", default=["af_heart", "am_michael"])
    args = ap.parse_args()

    from kokoro_onnx import Kokoro

    tts = Kokoro(str(args.models / "kokoro-v1.0.int8.onnx"), str(args.models / "voices-v1.0.bin"))
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for path in args.items:
        for it in json.loads(path.read_text()):
            if it["kind"] == "noise" or not it["text"]:
                continue
            voice = args.voices[int(it["id"]) % len(args.voices)]
            audio, sr = tts.create(it["text"], voice=voice, speed=1.0, lang="en-us")
            pcm = resample_poly(audio, 16000, sr)
            name = f"{path.stem}-{it['id']}.wav"
            with wave.open(str(args.out / name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
            manifest.append(
                {"file": name, "text": it["text"], "seconds": len(pcm) / 16000, "voice": voice}
            )
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    secs = [m["seconds"] for m in manifest]
    print(f"{len(manifest)} clips, mean {np.mean(secs):.2f} s, max {max(secs):.2f} s -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
