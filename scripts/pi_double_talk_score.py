# ruff: noqa: E501  (wide report lines)
"""Score a Pi double-talk run on the laptop: was what the person read still understood?

`pi_echo_check.py doubletalk` records the microphone while the assistant's reply plays and a person
reads a known sentence at the beep. Copy the run folder to the laptop and:

    uv run --group laptop python scripts/pi_double_talk_score.py out/pi/<run-folder>

For each trial it decodes the recording from just after the beep with Parakeet and reports the word
error against the sentence that was read, next to what the detectors did. Compare two runs (echo
cancellation on and off, one device against another) to see whether cancellation costs accuracy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import score_tries as st  # noqa: E402

from doc_chat import pi_check as pc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--after", type=float, default=0.25, help="skip this long after the beep (s)")
    ap.add_argument("--len", type=float, default=7.0, help="decode this long after that (s)")
    args = ap.parse_args()

    trials = json.loads((args.run / "trials.json").read_text())
    sc = st.load_compare()
    rec = st.recognizer([], None, False, 4)
    edits = words = 0
    print("| trial | said | heard | word error | its own voice tripped | you, s after beep |")
    print("|---|---|---|---|---|---|")
    for t in trials:
        mic = pc.read_wav(args.run / t["file"])
        start = int((t["beep_at_s"] + args.after) * pc.SR)
        seg = np.concatenate(
            [
                np.zeros(pc.SR // 2, np.float32),
                mic[start : start + int(args.len * pc.SR)],
                np.zeros(pc.SR, np.float32),
            ]
        )
        stream = rec.create_stream()
        stream.accept_waveform(pc.SR, seg)
        rec.decode_stream(stream)
        heard = str(stream.result.text).strip()
        ref = sc.norm_words(t["prompt"])
        e = sc.word_edits(ref, sc.norm_words(heard))
        edits, words = edits + e, words + len(ref)
        own = {d: len(v) for d, v in t["before_beep"].items()}
        you = {d: (v[:1] or "-") for d, v in t["after_beep"].items()}
        print(f"| {t['trial']} | {t['prompt']} | {heard} | {e}/{len(ref)} | {own} | {you} |")
    print(f"\nword error over {len(trials)} trials: {edits / max(words, 1):.1%} ({edits}/{words})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
