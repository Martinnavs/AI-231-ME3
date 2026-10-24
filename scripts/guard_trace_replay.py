"""Replay the barge-in guard's loudness stage from a session's guard trace, with variants.

    uv run --group laptop python scripts/guard_trace_replay.py out/sessions/agent-interrupt-ab-aec

`doc_chat --trace-guard --record DIR` writes DIR/guard.jsonl: one row per 100 ms microphone block
with the block's level, the played level, the room noise and what each stage said. The Silero stage
cannot be re-run from the trace (it needs the audio), so its recorded answer (`model`) is kept and
only the loudness stage is replayed: the same `EchoModel` settings as live (quantile 0.95, window
60, margin 1.2, prior 0.8, level 3 of 8) unless changed below.

Variant "warm-up floor": for the first `warmup_s` of every reply the bar is at least
`floor x played` (the guard's echo model learns from cancelled audio and its bar sits near the room
floor; a reply's first second, while the canceller settles, leaks more than that). The echo model
still learns against the ordinary bar, so it learns the same thing as live.

Per reply: an early fire (before the cue minus 0.1 s, or anywhere when the reply had no cue) is a
fire on the assistant's own voice; the first fire after it is the cued interruption. After a fire
the live reply stopped, so a replay cannot say what the rest of an early-fired reply would have
done: for those only "would it still have fired in the recorded part" is known.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Variant:
    name: str
    warmup_s: float = 0.0
    floor: float = 0.0
    min_coupling: float = 0.0


def replay(rows: list[dict[str, Any]], v: Variant) -> list[bool]:
    """The guard's decision for every row under variant `v` (Silero stage as recorded)."""
    ratios: deque[float] = deque(maxlen=60)
    out: list[bool] = []
    above: deque[bool] = deque(maxlen=8)
    reply, t0 = None, None
    for r in rows:
        if r["reply"] != reply:
            reply, t0 = r["reply"], None
            above.clear()
        noise, level, played = r["noise"], r["level"], r["played"]
        if not r["answering"]:
            out.append(bool(r["model"]) and level > max(noise * 3.0, 0.02))
            continue
        if t0 is None and played > 0:
            t0 = r["t"]
        if len(ratios) < 4:
            coupling = 0.8
        else:
            ordered = sorted(ratios)
            pos = 0.95 * (len(ordered) - 1)
            lo = int(pos)
            hi = min(lo + 1, len(ordered) - 1)
            coupling = ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
        coupling = max(coupling, v.min_coupling)
        extra = 1.2 * coupling * played if played > 0.01 else 0.0
        bar = max(noise * 4.0, 0.02, extra)
        guarded = bar
        if v.warmup_s and t0 is not None and r["t"] - t0 < v.warmup_s:
            guarded = max(bar, v.floor * played)
        above.append(level > guarded)
        fired = level > guarded and sum(above) >= 3
        if played > 0.01 and level <= bar:
            ratios.append(level / played)
        out.append(bool(r["model"]) and fired)
    return out


def score(rows: list[dict[str, Any]], fired: list[bool]) -> dict[str, Any]:
    by: dict[int, list[tuple[dict[str, Any], bool]]] = {}
    for r, f in zip(rows, fired, strict=True):
        by.setdefault(r["reply"], []).append((r, f))
    early, hits, cued, delays = 0, 0, 0, []
    for items in by.values():
        playing = [r for r, _ in items if r["answering"] and r["played"] > 0]
        if not playing:
            continue
        t0 = playing[0]["t"]
        cue = next((r["cue_s"] for r, _ in items if r.get("cue_s")), None)
        cut = t0 + cue - 0.1 if cue else float("inf")
        fires = [r["t"] for r, f in items if f and r["answering"]]
        if any(t < cut for t in fires):
            early += 1
            continue
        if cue:
            cued += 1
            after = [t for t in fires if t >= cut]
            if after:
                hits += 1
                delays.append(after[0] - (t0 + cue))
    delays.sort()
    return {
        "early": early,
        "cued_hits": f"{hits}/{cued}",
        "cue_to_fire_p50": round(delays[len(delays) // 2], 2) if delays else None,
    }


VARIANTS = (
    [Variant("live (baseline)")]
    + [
        Variant(f"warm-up {w:g}s floor {f:g}x played", warmup_s=w, floor=f)
        for w in (1.0, 1.5)
        for f in (0.3, 0.4, 0.5)
    ]
    + [Variant(f"coupling >= {c:g}", min_coupling=c) for c in (0.25, 0.35)]
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sessions", nargs="+", type=Path)
    args = ap.parse_args(argv)
    for s in args.sessions:
        rows = [json.loads(line) for line in (s / "guard.jsonl").read_text().splitlines()]
        base = replay(rows, VARIANTS[0])
        recorded = [bool(r["fired"]) for r in rows]
        agree = sum(a == b for a, b in zip(base, recorded, strict=True))
        print(
            f"== {s.name}: baseline replay agrees with the recorded decision on "
            f"{agree}/{len(rows)} blocks; recorded: {score(rows, recorded)}"
        )
        for v in VARIANTS:
            print(f"   {v.name:<34} {score(rows, replay(rows, v))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
