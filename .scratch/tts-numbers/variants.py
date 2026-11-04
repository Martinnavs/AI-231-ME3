"""Which spelling does Kitten speak so that Parakeet hears the right thing? Local only."""
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
print("== AM spellings (want AM/am)")
for label, spell in [("ay em (current)", "ay em"), ("A M", "A M"), ("a m", "a m"), ("A.M.", "A.M."), ("ei em", "ei em"),
                     ("eh em", "eh em"), ("A-M", "A-M"), ("ae em", "ae em"), ("Ay Em", "Ay Em"), ("A, M", "A, M")]:
    heard = [hear(f"It's nine fifty-eight {spell}."), hear(f"Alarm set for six thirty {spell} tomorrow.")]
    print(f"{label:16} | {heard[0]} | {heard[1]}")
print("\n== number formats (want the digits back)")
def words(n, style):
    w = num2words(n)
    if style == "plain": return w.replace(" and ", " ")
    if style == "comma": return re.sub(r"(thousand|million|billion) ", r"\1, ", w.replace(" and ", " "))
    if style == "and": return w
nums = [8849, 29032, 3932, 12500, 100000, 2500000, 38404, 7000, 1400000000, 999, 150000]
score = {}
for style in ("plain", "comma", "and"):
    ok = 0
    for n in nums:
        h = hear(f"The value is {words(n, style)} meters.")
        got = re.sub(r"[^\d]", "", h.replace(",", ""))
        good = got == str(n); ok += good
        if not good: print(f"  [{style}] {n}: heard {h!r}")
    score[style] = ok
print("score (of %d):" % len(nums), score)
