"""Decode every real turn of the spoken sessions with three name lists and count right and wrong spellings."""
import json, re, sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, "scripts"); sys.path.insert(0, "src")
import score_tries as st
from lan_speech.hotwords import load_entities
from lan_speech.hybrid_stt import pad_with_zeros, trim_silence

SR = 16000
docs = [e.name for e in load_entities(Path("config/entities-docs.json"))]
ph = [e.name for e in load_entities(Path("config/entities.json"))]
demo = ["Baguio", "Talisay", "Mandaue", "Steinbeck", "John Steinbeck", "Dune", "Frank Herbert", "Herbert", "Anne Carson",
        "John Berger", "Ways of Seeing", "East of Eden", "Googleplex", "Opalite", "Taylor Swift", "MC Hammer", "Anthropic"]
recs = {"A docs list (what the sessions used)": st.recognizer(docs, 1.0, True, 4),
        "B config/entities.json": st.recognizer(ph, 1.0, True, 4),
        "C entities.json + demo names": st.recognizer(ph + demo, 1.0, True, 4)}

def decode(rec, pcm):
    x = pad_with_zeros(trim_silence(pcm, SR, 0.3, 0.4), SR, 0.5, 1.0)
    s = rec.create_stream(); s.accept_waveform(SR, x); rec.decode_stream(s)
    return str(s.result.text).strip()

turns = []
for sess in ("fillers-4", "fillers-5", "fillers-6", "speaking-1", "speaking-2"):
    base = Path("out/sessions") / sess
    for line in open(base / "turns.jsonl"):
        r = json.loads(line); wav = base / "audio" / f"turn{r['turn']:02d}.wav"
        if wav.exists():
            with wave.open(str(wav)) as f: pcm = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
            turns.append((sess, r["turn"], r.get("heard") or r["question"], pcm))
out = {k: [decode(rec, pcm) for _s, _t, _h, pcm in turns] for k, rec in recs.items()}
json.dump({"turns": [(s, t, h) for s, t, h, _ in turns], "out": out}, open(".scratch/ablations/entity_lists.json", "w"), indent=1)

RIGHT = {"Baguio": r"\bbaguio\b", "Cebu": r"\bcebu\b", "Talisay": r"\btalisay\b", "Mandaue": r"\bmandaue\b", "Taguig": r"\btaguig\b",
         "Makati": r"\bmakati\b", "Steinbeck": r"\bsteinbeck\b", "Dune": r"\bdune\b", "Googleplex": r"\bgoogleplex\b", "Opalite": r"\bopalite\b"}
WRONG = {"Baguio": r"\bba[gc]+[il]+o\b|\bbaggio\b|\bbagio\b|\bbalio\b|\bbaguyo\b|\bbad yo\b", "Cebu": r"\bsebu\b|\bcebo\b|\bsaboo\b",
         "Talisay": r"\btaisai\b|\btalis[ae]y?\b(?<!talisay)", "Mandaue": r"\bmandawa\b|\bmandaway\b|\bmandau\b", "Taguig": r"\btaguik\b|\btagig\b",
         "Makati": r"\bmakaty\b|\bmacati\b", "Steinbeck": r"\bsteinberg\b|\bjoin steinbeck|\bjan steinbeck", "Dune": r"\bjune\b",
         "Googleplex": r"\bgoogle plex\b|\bgoogle plus\b", "Opalite": r"\bopal(ight|ite)\b(?<!opalite)|\bapalite\b"}
def count(pat, texts): return sum(len(re.findall(pat, t.lower())) for t in texts)
cols = {"live (stored)": [h for _s, _t, h, _p in turns], **out}
print(f"{len(turns)} real turns\n{'name':<12}" + "".join(f"{k[:30]:>34}" for k in cols))
tot = {k: [0, 0] for k in cols}
for n in RIGHT:
    cells = []
    for k, t in cols.items():
        r, w = count(RIGHT[n], t), count(WRONG.get(n, r"(?!x)x"), t)
        tot[k][0] += r; tot[k][1] += w
        cells.append(f"right {r}, wrong {w}")
    print(f"{n:<12}" + "".join(f"{c:>34}" for c in cells))
print(f"{'TOTAL':<12}" + "".join(f"{'right %d, wrong %d' % tuple(v):>34}" for v in tot.values()))
changed = {k: sum(1 for a, b in zip(out["A docs list (what the sessions used)"], v) if a != b) for k, v in out.items()}
print("turns whose text differs from list A:", changed)
