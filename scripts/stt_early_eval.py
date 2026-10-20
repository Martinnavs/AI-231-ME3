# ruff: noqa: E501  (wide report lines)
"""Does deciding at the first short pause change the words? Scored on clips with known text.

For each clip the pause detector runs as it would on the server; the last pause (after the final
stretch of speech) marks where an early decode would have started. The audio up to there is
decoded the way the server decodes (trim, zero padding, hotwords) and compared with decoding the
whole clip, both against what was actually said.

    uv run --group laptop python scripts/stt_early_eval.py [--turns out/tries]
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import score_tries as st  # noqa: E402

from lan_speech.hotwords import load_entities  # noqa: E402
from lan_speech.hybrid_stt import pad_with_zeros, trim_silence  # noqa: E402
from lan_speech.pause import PauseDetector  # noqa: E402

SR = 16000


def last_pause_end(pcm: np.ndarray, hold_s: float) -> int | None:
    """Sample index at which an early decode would start and survive, or None.

    The same rules as the server: a pause starts it, anything above 1.5x the noise floor after it
    (more speech, or the soft end of a word) cancels it. What is left at the end is the decode
    that would have been used."""
    n = len(pcm) // 1600
    levels = np.sqrt(np.mean(pcm[: n * 1600].reshape(n, 1600) ** 2, axis=1))
    det = PauseDetector(hold_s, noise=float(np.percentile(levels, 10)))
    found = None
    for i, rms in enumerate(levels):
        voiced, pause = det.feed(float(rms), 0.1)
        if found is not None and (voiced or det.above_floor(float(rms))):
            found = None
        if pause:
            found = (i + 1) * 1600
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=Path, default=Path("out/tries"))
    ap.add_argument("--holds", type=float, nargs="+", default=[0.2, 0.3])
    ap.add_argument("--names", type=Path, default=Path("config/entities.json"))
    args = ap.parse_args()
    sc = st.load_compare()
    rec = st.recognizer([e.name for e in load_entities(args.names)], 1.0, True, 4)

    def decode(pcm: np.ndarray) -> str:
        x = pad_with_zeros(trim_silence(pcm, SR, 0.3, 0.4), SR, 0.5, 1.0)
        s = rec.create_stream()
        s.accept_waveform(SR, x)
        rec.decode_stream(s)
        return str(s.result.text).strip()

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
    tsv = args.turns / "truth.tsv"
    if tsv.exists():
        for line in tsv.read_text().splitlines():
            n, text = line.split("\t", 1)
            truth[int(n)] = text
        for j in sorted(args.turns.glob("turn-*.json")):
            n = int(j.stem.rsplit("-", 1)[1])
            if n in truth:
                with wave.open(str(j.with_suffix(".wav"))) as w:
                    pcm = (
                        np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
                        / 32768
                    )
                sets["live session"].append((pcm, truth[n]))

    print(
        "| set | pause | clips with an early start | WER full clip | WER early decode | words changed by deciding early |"
    )
    print("|---|---|---|---|---|---|")
    for name, clips in sets.items():
        full = [decode(p) for p, _ in clips]
        for hold in args.holds:
            e_full = e_early = words = changed = used = 0
            for (pcm, text), f in zip(clips, full, strict=True):
                ref = sc.norm_words(text)
                words += len(ref)
                end = last_pause_end(pcm, hold)
                early = decode(pcm[:end]) if end is not None else f
                used += end is not None
                e_full += sc.word_edits(ref, sc.norm_words(f))
                e_early += sc.word_edits(ref, sc.norm_words(early))
                changed += sc.word_edits(sc.norm_words(f), sc.norm_words(early))
            print(
                f"| {name} | {hold} s | {used} of {len(clips)} | {e_full / words:.1%} | {e_early / words:.1%} | {changed} of {words} ({changed / words:.1%}) |",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
