"""Round 2: decimals, billions, dates, PM. Local only."""
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
def w(n): return re.sub(r"(thousand|million|billion) ", r"\1, ", num2words(n).replace(" and ", " "))
print("== PM spellings")
for spell in ("pee em", "P M", "p m"):
    print(f"  {spell:7} | {hear(f'It is eleven oh four {spell} in Tokyo.')} | {hear(f'Alarm set for seven {spell} tomorrow.')}")
print("\n== billions (want 1.4 billion = 1,400,000,000)")
for label, text in [("one billion four hundred million", "The population is one billion, four hundred million people."),
                    ("one point four billion", "The population is one point four billion people."),
                    ("1.4 billion digits", "The population is 1.4 billion people."),
                    ("seven point nine billion", "The world has seven point nine billion people."),
                    ("three hundred forty million", "The country has three hundred forty million people.")]:
    print(f"  {label:34} | {hear(text)}")
print("\n== decimals")
for label, text in [("8,848.86 words+point", f"It is {w(8848)} point eight six meters tall."),
                    ("rounded 8,849", f"It is {w(8849)} meters tall."),
                    ("29,031.7 words+point", f"That is {w(29031)} point seven feet."),
                    ("rounded 29,032", f"That is {w(29032)} feet."),
                    ("3,931.67 words+point", f"Gold is {w(3931)} point six seven dollars."),
                    ("3,931 dollars and 67 cents", f"Gold is {w(3931)} dollars and {w(67)} cents."),
                    ("1.7", "It has one point seven gigabytes."), ("0.35", "The ratio is zero point three five."),
                    ("12.5 percent", "The rate is twelve point five percent.")]:
    print(f"  {label:30} | {hear(text)}")
print("\n== dates (want June 13, 2023 / November 4, 2026)")
for label, text in [("current style", "He died on June thirteen twenty twenty-three."),
                    ("ordinal+comma", "He died on June thirteenth, twenty twenty-three."),
                    ("ordinal no year comma", "He died on June thirteenth twenty twenty-three."),
                    ("Nov 4 2026 ordinal", "Today is Wednesday, November fourth, twenty twenty-six."),
                    ("Oct 1 2024 ordinal", "He has served since October first, twenty twenty-four."),
                    ("year two thousand five", "It was published in two thousand five."),
                    ("year two thousand and five", "It was published in two thousand and five.")]:
    print(f"  {label:26} | {hear(text)}")
