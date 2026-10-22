"""Summarize the unattended double-talk runs (scripts/pi_synth_doubletalk.py) into one table.

    uv run --group laptop python scripts/pi_synth_report.py out/pi/*-synth-* [--no-wer] [--json out.json]

Per run (one row): how loud the assistant's own voice is at the microphone, how many of its
trials tripped each detector (``assistant_only`` scenario, the whole reply), and how well the user's
words survive: word error when the user talks alone (the baseline for this microphone path) and
over the reply (``both``). Word error is Parakeet's, decoded from just after the beep.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import score_tries as st  # noqa: E402

from doc_chat import pi_check as pc  # noqa: E402

DETECTORS = ("energy", "silero_250ms", "silero_150ms")


def load(folder: Path) -> list[dict[str, Any]]:
    p = folder / "trials.json"
    return json.loads(p.read_text()) if p.exists() else []


def word_error(folder: Path, trials: list[dict[str, Any]], rec: Any, sc: Any) -> dict[str, Any]:
    edits = words = 0
    heard_all = []
    for t in trials:
        if "file" not in t:
            continue
        mic = pc.read_wav(folder / t["file"])
        start = int((t["beep_at_s"] + 0.25) * pc.SR)
        seg = np.concatenate(
            [np.zeros(pc.SR // 2, np.float32), mic[start : start + 7 * pc.SR], np.zeros(pc.SR, np.float32)]
        )
        stream = rec.create_stream()
        stream.accept_waveform(pc.SR, seg)
        rec.decode_stream(stream)
        heard = str(stream.result.text).strip()
        ref = sc.norm_words(t["prompt"])
        edits += sc.word_edits(ref, sc.norm_words(heard))
        words += len(ref)
        heard_all.append(heard)
    return {"edits": edits, "words": words, "wer": edits / words if words else None, "heard": heard_all}


def summarize(run: Path, rec: Any, sc: Any) -> dict[str, Any]:
    meta = json.loads((run / "run.json").read_text())
    row: dict[str, Any] = {"run": run.name, "label": meta["label"], "mode": meta["mode"],
                           "voice": meta["voice"], "user_gain_db": meta["user_gain_db"]}  # fmt: skip
    own = [t for t in load(run / "assistant_only") if "assistant" in t]
    if own:
        row["trials"] = len(own)
        row["own_above_room_db_median"] = round(float(np.median([t["assistant"]["above_floor_db_median"] for t in own])), 1)
        row["own_above_room_db_worst_p95"] = round(max(t["assistant"]["above_floor_db_p95"] for t in own), 1)
        row["own_first_second_db_max"] = round(max(t["assistant"]["above_floor_db_first_second"] for t in own), 1)
        row["tripped"] = {
            d: sum(1 for t in own if t["assistant"]["onsets_after_playback_start_s"].get(d))
            for d in DETECTORS
        }  # fmt: skip
    for scenario in ("user_only", "both"):
        trials = load(run / scenario)
        if not trials:
            continue
        row[f"{scenario}_failed_sync"] = sum(1 for t in trials if "error" in t)
        if rec is not None:
            row[scenario] = word_error(run / scenario, trials, rec, sc)
    both = [t for t in load(run / "both") if "after_beep" in t]
    if both:  # onsets seen in the 1.5 s after the user starts, minus none: raw counts, see assistant_only
        row["both_onset_within_1.5s"] = {
            d: sum(1 for t in both if any(-0.3 <= x <= 1.5 for x in t["after_beep"].get(d, [])))
            for d in DETECTORS
        }  # fmt: skip
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("runs", type=Path, nargs="+")
    ap.add_argument("--no-wer", action="store_true", help="skip the Parakeet pass")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    sc = st.load_compare()
    rec = None if args.no_wer else st.recognizer([], None, False, 4)
    rows = [summarize(r, rec, sc) for r in sorted(args.runs) if (r / "run.json").exists()]
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1))

    def pct(x: Any) -> str:
        return "-" if not x or x.get("wer") is None else f"{x['wer']:.0%} ({x['edits']}/{x['words']})"

    print("| run | own voice above room (median / worst p95 / first s, dB) | tripped energy / s250 / s150 | user alone WER | user over reply WER |")
    print("|---|---|---|---|---|")
    for r in rows:
        tr = r.get("tripped", {})
        n = r.get("trials", 0)
        print(
            f"| {r['label']} | {r.get('own_above_room_db_median', '-')} / {r.get('own_above_room_db_worst_p95', '-')} / {r.get('own_first_second_db_max', '-')} "
            f"| {' / '.join(f'{tr.get(d, 0)}/{n}' for d in DETECTORS)} | {pct(r.get('user_only'))} | {pct(r.get('both'))} |"
        )  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
