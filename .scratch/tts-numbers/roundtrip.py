"""Kitten text -> audio -> Parakeet text. Shows what is actually spoken. Local only, no network."""
import os, sys, json
from pathlib import Path
import numpy as np
sys.path.insert(0, "src")
from lan_speech.engines import KittenTTSEngine
from lan_speech.hybrid_stt import ParakeetTranscriber
from lan_speech.models import PARAKEET_DIR

def resample(pcm: bytes, src=24000, dst=16000):
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(len(x) * dst / src)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)

tts = KittenTTSEngine()
stt = ParakeetTranscriber(Path("models") / PARAKEET_DIR, [])
cases = json.load(open(sys.argv[1]))
for text in cases:
    heard = stt(resample(tts.synthesize_chunk(text)), 16000)
    print(f"SAID : {text}\nHEARD: {heard}\n")
