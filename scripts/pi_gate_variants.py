# ruff: noqa: E501
"""Replay alternative barge-in gates on recorded Silero confidences, on both sides of the trade.

    uv run python scripts/pi_gate_variants.py out/pi/<live-run> [--split 90]

Detection: for each key hold in a live pilot run, does the variant interrupt, and how long after the
voice began. False triggers: the same variant over the no-user selftest recordings in out/pi/ (the
assistant's own voice only), so a looser gate has to show it does not fire on that. 'Speaking' is the
counterfactual (the clip's natural length), so a variant is judged as if the real gate had not
already stopped the assistant. With --split, the first part of the run is for choosing and the rest
for checking.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from doc_chat import pi_check as pc  # noqa: E402
from doc_chat import pi_pilot as pp  # noqa: E402
from doc_chat.bargein import SileroOnset  # noqa: E402

W = pp.WINDOW_S

# name: (confidence, rule, parameter)
VARIANTS: dict[str, tuple[float, str, Any]] = {
    "spec: 8 in a row @0.7": (0.7, "strict", 0.25),
    "6 in a row @0.7 (150 ms)": (0.7, "strict", 0.15),
    "8 in a row @0.5": (0.5, "strict", 0.25),
    "allow 1 dip @0.7": (0.7, "tol1", 0.25),
    "allow 1 dip @0.5": (0.5, "tol1", 0.25),
    "leaky count @0.7": (0.7, "leaky", 0.25),
    "leaky count @0.6": (0.6, "leaky", 0.25),
    "leaky count @0.5": (0.5, "leaky", 0.25),
    "7 of last 10 @0.7": (0.7, "kofn", (7, 10)),
    "7 of last 10 @0.5": (0.5, "kofn", (7, 10)),
}


def interrupts(
    conf: Any,
    speaking: Any,
    thr: float,
    rule: str,
    param: Any,
    times: Any = None,
    holds: list[tuple[float, float]] | None = None,
) -> list[int]:
    """Window indexes at which the variant would interrupt. After an interrupt the assistant is
    quiet until the key is released plus 1 s (3 s at most), as in the live pilot; with no key
    holds that is 3 s."""
    out: list[int] = []
    heard, miss, frozen, until = 0.0, 0, False, 0.0
    recent: deque[bool] = deque(maxlen=param[1] if rule == "kofn" else 1)
    for i, (c, sp) in enumerate(zip(conf, speaking, strict=True)):
        if not sp:
            heard, miss, frozen = 0.0, 0, False
            recent.clear()
            continue
        if frozen:
            if times is None or times[i] < until:
                continue
            heard, miss, frozen = 0.0, 0, False
            recent.clear()
        hit = c >= thr
        if rule == "strict":
            heard = heard + W if hit else 0.0
            done = heard >= param - 1e-9
        elif rule == "tol1":
            if hit:
                heard, miss = heard + W, 0
            else:
                miss += 1
                if miss >= 2:
                    heard, miss = 0.0, 0
            done = heard >= param - 1e-9
        elif rule == "leaky":
            heard = max(heard + (W if hit else -W), 0.0)
            done = heard >= param - 1e-9
        else:
            recent.append(hit)
            done = hit and sum(recent) >= param[0]
        if done:
            out.append(i)
            frozen = True
            if times is not None:
                now = times[i] + W
                up = next((u for d, u in (holds or []) if d - 0.3 <= now <= u + 0.5), now)
                until = min(max(up + 1.0, now + 1.0), now + 3.0)
    return out


def load_run(folder: Path) -> dict[str, Any]:
    meta = json.loads((folder / "run.json").read_text())
    ev = json.loads((folder / "events.json").read_text())
    c = json.loads((folder / "conf.json").read_text())
    t = np.array(c["t"]) - W
    lengths = {
        p.name: len(pc.read_wav(p)) / pc.SR
        for p in Path("tests/fixtures/pi_echo").glob("reply-*.wav")
    }
    spans = [(e["t"], e["t"] + lengths[e["name"]]) for e in ev if e["kind"] == "clip_start"]
    speaking = np.array([any(a <= x <= b for a, b in spans) for x in t])
    conf = np.array(c["conf"])
    holds = pp._holds(ev, meta["hold_key"])
    onsets = []
    for d, u in holds:
        idx = np.nonzero((t >= d - 0.5) & (t <= u) & (conf >= 0.5))[0]
        onsets.append(float(t[idx[0]]) if len(idx) else d + 0.534)
    over = [
        (d, u, o)
        for (d, u), o in zip(holds, onsets, strict=True)
        if any(a <= o <= b for a, b in spans)
    ]
    return {"t": t, "conf": conf, "speaking": speaking, "over": over, "holds": holds}


def selftest_exposure() -> dict[str, list[tuple[Any, float]]]:
    """Confidence sequences of the assistant playing alone, grouped by condition."""
    groups: dict[str, list[tuple[Any, float]]] = {}
    for d in sorted(Path("out/pi").glob("*selftest-*")):
        meta = json.loads((d / "run.json").read_text())
        label = meta.get("label", d.name)
        key = "C" if "split" in label else "E"
        vol = next((v for v in ("vol40", "vol60", "vol75", "vol90") if v in label), "vol40")
        for wav in sorted(d.glob("mic-reply-*.wav")):
            m = pc.read_wav(wav)
            pcm = (np.clip(m, -1, 1) * 32767).astype(np.int16)
            det = SileroOnset("assets/silero_vad.onnx", min_s=1e9, keep_history=True)
            for i in range(0, len(pcm) - pc.BLOCK + 1, pc.BLOCK):
                det.feed(pcm[i : i + pc.BLOCK].tobytes())
            groups.setdefault(f"{key} {vol}", []).append((np.array(det.history), len(m) / pc.SR))
    return groups


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument(
        "--split", type=float, help="seconds: first part for choosing, rest for checking"
    )
    args = ap.parse_args()
    run = load_run(args.run)
    exposure = selftest_exposure()
    cols = sorted(exposure)
    print(f"run {args.run.name}: {len(run['over'])} barge-ins over assistant speech\n")
    head = (
        "| variant | interrupted | median s to interrupt | false in the run | "
        + " | ".join(f"{c}: false/min" for c in cols)
        + " |"
    )
    print(head)
    print("|" + "---|" * (4 + len(cols)))
    parts = (
        [("all", 0.0, 1e9)]
        if not args.split
        else [("first", 0.0, args.split), ("rest", args.split, 1e9)]
    )
    for part, lo, hi in parts:
        if args.split:
            print(
                f"| **{part} part ({lo:.0f}-{min(hi, run['t'][-1]):.0f} s)** |"
                + " |" * (3 + len(cols))
            )
        for name, (thr, rule, param) in VARIANTS.items():
            idx = interrupts(run["conf"], run["speaking"], thr, rule, param, run["t"], run["holds"])
            times = np.array([run["t"][i] + W for i in idx])
            hit, lat = 0, []
            n = 0
            for d, u, onset in run["over"]:
                if not lo <= onset < hi:
                    continue
                n += 1
                m = times[(times >= d - 0.3) & (times <= u + 0.5)]
                if len(m):
                    hit += 1
                    lat.append(m[0] - onset)
            windows = [(d - 0.3, u + 0.5) for d, u in run["holds"]]
            sel = times[(times >= lo) & (times < hi)]
            false = sum(1 for x in sel if not any(a <= x <= b for a, b in windows))
            row = []
            for c in cols:
                total_min = sum(sec for _, sec in exposure[c]) / 60
                n_false = sum(
                    len(
                        interrupts(
                            h, np.ones(len(h), bool), thr, rule, param, np.arange(len(h)) * W, []
                        )
                    )
                    for h, _ in exposure[c]
                )
                row.append(f"{n_false} ({n_false / total_min:.1f})")
            med = f"{np.median(lat):.2f}" if lat else "-"
            print(f"| {name} | {hit}/{n} | {med} | {false} | " + " | ".join(row) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
