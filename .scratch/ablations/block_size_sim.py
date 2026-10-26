"""When does the client's pause rule (0.6 s) fire on real turns, with 100 ms vs 20 ms capture blocks?
Same detector (lan_speech.pause.PauseDetector), same audio, same noise hint; only the block size changes."""
import json
import statistics as st
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")
from lan_speech.pause import PauseDetector


def fire_time(pcm: np.ndarray, block: int, offset: int) -> float | None:
    x = pcm[offset:]
    n = len(x) // block
    levels = np.sqrt(np.mean(x[: n * block].reshape(n, block) ** 2, axis=1))
    det = PauseDetector(0.6, noise=float(np.percentile(levels, 10)))
    dt = block / 16000
    for i, rms in enumerate(levels):
        _voiced, pause = det.feed(float(rms), dt)
        if pause:
            return (offset + (i + 1) * block) / 16000  # when the block that completed the hold arrives
    return None


diffs = []
for sess in ("fillers-4", "fillers-5", "fillers-6"):
    base = Path("out/sessions") / sess
    for line in open(base / "turns.jsonl"):
        r = json.loads(line)
        wav = base / "audio" / f"turn{r['turn']:02d}.wav"
        if not wav.exists():
            continue
        with wave.open(str(wav)) as f:
            pcm = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
        for off in (0, 320, 640, 960, 1280):  # five alignments of the block grid against the speech
            a, b = fire_time(pcm, 1600, off), fire_time(pcm, 320, off)
            if a is not None and b is not None:
                diffs.append(a - b)
diffs.sort()
print(
    f"{len(diffs)} (turn, alignment) pairs: 20 ms blocks fire earlier by median "
    f"{st.median(diffs) * 1000:.0f} ms, mean {st.mean(diffs) * 1000:.0f} ms, "
    f"p10 {diffs[int(0.1 * len(diffs))] * 1000:.0f}, p90 {diffs[int(0.9 * len(diffs))] * 1000:.0f}"
)
