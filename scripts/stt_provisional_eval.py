# ruff: noqa: E501  (wide report lines)
"""How often does a provisional transcript equal the verified final? (the speculative-start precedent)

Ticket 12 / design review §3: start the LLM on an early transcript, hold its audio, release it if the
final matches (lowercase, punctuation stripped), cancel and restart otherwise. This measures the
match rate at the level of the whole utterance, for two sources of the early text:
  kroko     the streaming model's interim text when the pause is detected (free: it already runs)
  parakeet  a Parakeet decode of the audio up to the pause (costs a background decode)
against the verified final (Parakeet on the whole clip), and both against what was actually said.

    uv run --group laptop python scripts/stt_provisional_eval.py
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import score_tries as st  # noqa: E402
from stt_early_eval import last_pause_end  # noqa: E402

from lan_speech.engines import SherpaSTTEngine  # noqa: E402
from lan_speech.hotwords import load_entities  # noqa: E402
from lan_speech.hybrid_stt import pad_with_zeros, trim_silence  # noqa: E402

SR = 16000


def main() -> int:
    sc = st.load_compare()
    rec = st.recognizer([e.name for e in load_entities(Path("config/entities.json"))], 1.0, True, 4)
    kroko = SherpaSTTEngine("models", threads=2, model="kroko")

    def parakeet(pcm: np.ndarray) -> str:
        x = pad_with_zeros(trim_silence(pcm, SR, 0.3, 0.4), SR, 0.5, 1.0)
        s = rec.create_stream()
        s.accept_waveform(SR, x)
        rec.decode_stream(s)
        return str(s.result.text).strip()

    def kroko_interim(pcm: np.ndarray) -> str:
        stream = kroko.create_stream(SR)
        last = ""
        pcm16 = (np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes()
        for i in range(0, len(pcm16), 3200):
            stream.accept_waveform(SR, pcm16[i : i + 3200])
            res = stream.decode()
            if res is not None and res.text:
                last = res.text
        return last

    sets: dict[str, list[tuple[np.ndarray, str]]] = {"hard": [], "held-out": [], "live session": []}
    for name, items, folder in (
        ("hard", "bench/stt_hard.json", "bench/mic_hard"),
        ("held-out", "bench/stt_heldout.json", "bench/mic_heldout"),
    ):
        for it in json.loads(Path(items).read_text()):
            if it["kind"] != "noise" and it["text"]:
                sets[name].append(
                    (
                        sc.read_wav(Path(folder) / f"{it['id']}.wav").astype(np.float32) / 32768,
                        it["text"],
                    )
                )
    truth = {}
    for line in Path("out/tries/truth.tsv").read_text().splitlines():
        n, text = line.split("\t", 1)
        truth[int(n)] = text
    for j in sorted(Path("out/tries").glob("turn-*.json")):
        n = int(j.stem.rsplit("-", 1)[1])
        if n in truth:
            with wave.open(str(j.with_suffix(".wav"))) as w:
                sets["live session"].append(
                    (
                        np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
                        / 32768,
                        truth[n],
                    )
                )

    nw = sc.norm_words
    print(
        "Match = the provisional text, normalised, is word for word the verified final (so the speculative answer can be released).\n"
    )
    print(
        "| set | provisional from | pause | turns | provisional == final | cancelled | provisional WER vs said | final WER vs said |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for name, clips in sets.items():
        finals = [parakeet(p) for p, _ in clips]
        wer_final = sum(
            sc.word_edits(nw(t), nw(f)) for (_, t), f in zip(clips, finals, strict=True)
        ) / sum(len(nw(t)) for _, t in clips)
        for hold in (0.2,):
            for source in ("kroko", "parakeet"):
                match = n = 0
                err = words = 0
                for (pcm, text), final in zip(clips, finals, strict=True):
                    end = last_pause_end(pcm, hold)
                    if end is None:
                        continue  # no pause before the end: nothing speculative would start
                    prov = kroko_interim(pcm[:end]) if source == "kroko" else parakeet(pcm[:end])
                    n += 1
                    match += nw(prov) == nw(final)
                    err += sc.word_edits(nw(text), nw(prov))
                    words += len(nw(text))
                print(
                    f"| {name} | {source} text | {hold} s | {n} of {len(clips)} | {match} ({match / max(n, 1):.0%}) | {n - match} ({(n - match) / max(n, 1):.0%}) | {err / max(words, 1):.1%} | {wer_final:.1%} |",
                    flush=True,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
