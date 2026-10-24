"""Would a shorter pre-roll keep the assistant's echo out of an interruption's transcript? (offline)

    uv run --group laptop python scripts/preroll_eval.py out/sessions/agent-interrupt-ab-duck \
        [more sessions] --server ws://127.0.0.1:8767 [--keep 1.2,0.8,0.6,0.45,0.3]

After a barge-in, `doc_chat` hands the recogniser the last 1.2 s of microphone audio before the
guard fired (the pre-roll), then live audio; `--record` saves that turn as audio/turnNN.wav. The
guard needs about 0.3 s of speech, so most of the pre-roll is from before the user spoke: the
assistant's echo (raw sessions) or what the canceller left of it (`--aec` sessions, whose audio is
after the canceller). This trims the first (1.2 - keep) s of each saved interruption turn and
transcribes it again with a speech server (start one on loopback, see .scratch/echo-next/PLAN.md).

Only turns that were not a bare stop phrase were saved, so a lost "Stop." cannot show up here.
The untrimmed file is transcribed too: offline transcription of the whole file is not the same as
the live streamed result, so compare variants with each other, not with the live log.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from aec_offline import transcribe  # noqa: E402
from echo_prefix_eval import fuzzy_echo, strip_echo_prefix  # noqa: E402

RATE = 16000
PREROLL_S = 1.2


def read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--server", default="ws://127.0.0.1:8767")
    ap.add_argument("--keep", default="1.2,0.8,0.6,0.45,0.3", help="pre-roll seconds to keep")
    args = ap.parse_args(argv)
    keeps = [float(x) for x in args.keep.split(",")]
    items, meta = [], []
    for s in args.sessions:
        rows = [json.loads(line) for line in (s / "turns.jsonl").read_text().splitlines()]
        for i, r in enumerate(rows[:-1]):
            if r.get("interrupted") != "barge_in":
                continue
            nxt = rows[i + 1]
            wav = s / "audio" / f"turn{int(nxt['turn']):02d}.wav"
            if not wav.exists():
                continue
            x = read(wav)
            spoken = str(r.get("spoken") or "")
            meta.append((s.name, nxt["turn"], str(nxt.get("question")), spoken))
            for k in keeps:
                cut = int((PREROLL_S - k) * RATE)
                items.append((f"{s.name}|{nxt['turn']}|{k}", x[cut:]))
    text = asyncio.run(transcribe(items, args.server))
    echo_counts = {k: 0 for k in keeps}
    for name, turn, live, spoken in meta:
        print(f"== {name} turn {turn}   live: {live!r}")
        for k in keeps:
            t = text[f"{name}|{turn}|{k}"]
            echo = fuzzy_echo(t, spoken) or strip_echo_prefix(t, spoken) != t
            echo_counts[k] += echo
            print(f"   keep {k:>4} s{' E' if echo else '  '} {t!r}")
    print(f"\n{len(meta)} saved interruption turns; transcripts with the reply's words in front or")
    counts = ", ".join(f"keep {k:g} s: {n}" for k, n in echo_counts.items())
    print(f"made only of them (E): {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
