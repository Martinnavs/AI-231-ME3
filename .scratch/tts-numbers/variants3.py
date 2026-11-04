"""Round 3: is the garbling random, and do commas / rounding help? 3 repeats per cell. Local only."""
import re, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, "src")
from lan_speech.engines import KittenTTSEngine
from lan_speech.hybrid_stt import ParakeetTranscriber
from lan_speech.models import PARAKEET_DIR
tts = KittenTTSEngine()
from num2words import num2words
stt = ParakeetTranscriber(Path("models") / PARAKEET_DIR, [])
def hear(text):
    pcm = np.frombuffer(tts.synthesize_chunk(text), dtype=np.int16).astype(np.float32) / 32768.0
    x = np.interp(np.linspace(0, len(pcm) - 1, int(len(pcm) * 16000 / 24000)), np.arange(len(pcm)), pcm).astype(np.float32)
    return stt(x, 16000)
def say(value: float, commas: bool, rounded: bool) -> str:
    if rounded and value >= 1000: value = round(value)
    whole, frac = int(value), f"{value:.2f}".split(".")[1].rstrip("0") if value != int(value) else ""
    words = num2words(whole).replace(" and ", " ")
    if not commas: words = words.replace(",", "")
    if frac: words += " point " + " ".join(num2words(int(d)) for d in frac)
    return words
def digits(s):  # the number Parakeet wrote, commas removed
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    return m.group(0).replace(",", "") if m else ""
values = [29032, 29031.7, 21500, 24000, 33333, 45000, 68214, 8849, 56789, 74100, 92500, 31000, 42, 38404, 27000, 22222]
def say2(v, hyphen):
    t = say(v, True, False)
    return t if hyphen else t.replace("-", " ")
res = {}
for hyphen in (True, False):
    ok = 0; bad = []
    for v in values:
        want = str(int(v)) if v == int(v) else str(v)
        got = digits(hear(f"The value is {say2(v, hyphen)} meters."))
        ok += got == want
        if got != want: bad.append((v, got))
    print(f"hyphen={hyphen!s:5} {ok}/{len(values)}  wrong: {bad}")
