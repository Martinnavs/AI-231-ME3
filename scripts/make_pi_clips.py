"""Make the standard clips for the Pi echo checks (run on the laptop; the Pi needs no TTS).

Writes tests/fixtures/pi_echo/:
  reply-01..06.wav   what the "assistant" plays: two random sentences each (16 kHz mono)
  prompts.json       what the person reads in the double-talk test (random sentences, known text)

The sentences come from doc_chat.phrases, so the same seeds give the same clips on any machine.

    uv run --group laptop python scripts/make_pi_clips.py
"""

from __future__ import annotations

import json
import sys
import wave
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from doc_chat.phrases import phrases  # noqa: E402
from lan_speech.engines import SupertonicTTSEngine  # noqa: E402

OUT = Path("tests/fixtures/pi_echo")
SR = 16000


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tts = SupertonicTTSEngine(Path("models/supertonic-3"), total_steps=4, intra_op_threads=2)
    g = gcd(SR, tts.sample_rate)
    texts = {}
    for i in range(1, 7):
        text = " ".join(phrases(f"pi-reply-{i}", 2))
        x = np.frombuffer(tts.synthesize_chunk(text), np.int16).astype(np.float32) / 32768
        y = resample_poly(x, SR // g, tts.sample_rate // g)
        y = y / max(float(np.abs(y).max()), 1e-6) * 0.5  # same peak level for every clip
        with wave.open(str(OUT / f"reply-{i:02d}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((y * 32767).astype(np.int16).tobytes())
        texts[f"reply-{i:02d}.wav"] = text
        print(f"reply-{i:02d}.wav  {len(y) / SR:.1f} s  {text}")
    prompts = [" ".join(phrases(f"pi-say-{i}", 1)) for i in range(1, 7)]
    (OUT / "prompts.json").write_text(json.dumps({"replies": texts, "prompts": prompts}, indent=1))
    print("prompts:", *prompts, sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
