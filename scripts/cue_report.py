"""Interruptions, latency and echo from a doc_chat session recorded with --cue-beep.

    uv run --group laptop python scripts/cue_report.py out/sessions/mccarthy-3

With ``--cue-beep S --cue-every 2`` every second reply has a beep S seconds in, which is the cue to
talk over it; the other replies are quiet turns. The log therefore knows what was *supposed* to
happen, and this script compares it with what did:

* a cued reply that was interrupted after the beep is a hit (the time from the beep to the stop
  includes your reaction and the beep's own trip through the speaker, about 0.2 s);
* a cued reply that played to the end is a miss;
* an interruption of a quiet reply, or before the beep, is a false trigger (the assistant heard
  itself), and so is any ``ignored_echo`` record (its own words came back as "speech").
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


def classify(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hits: list[float] = []
    missed = quiet_ok = false_quiet = false_early = 0
    stops = echoes = pivots = 0
    for i, r in enumerate(rows):
        if r.get("ignored_echo"):
            echoes += 1
            continue
        if r.get("stop_word"):
            stops += 1
            continue
        cue = r.get("cue_s")
        kind = r.get("interrupted")
        after = r.get("interrupt_after_answer_start_ms")
        if kind == "barge_in":
            if cue is None:
                false_quiet += 1
            elif after is not None and after / 1000.0 < cue:
                false_early += 1
            elif after is not None:
                hits.append(after / 1000.0 - cue)
                nxt = rows[i + 1] if i + 1 < len(rows) else {}
                if nxt.get("pivot_from") and nxt.get("answer_words", 0) > 0:
                    pivots += (
                        1  # talked over it with "wait, stop, <question>" and got that answered
                    )
        elif kind is None and "answer_words" in r and r.get("llm_error") is None:
            if cue is None:
                quiet_ok += 1
            else:
                missed += 1
    return {
        "hits": hits,
        "missed": missed,
        "quiet_ok": quiet_ok,
        "false_quiet": false_quiet,
        "false_early": false_early,
        "ignored_echo": echoes,
        "stop_words": stops,
        "pivots": pivots,
    }


def stats(values: list[float]) -> str:
    if not values:
        return "-"
    v = sorted(values)
    p95 = v[min(len(v) - 1, round(0.95 * (len(v) - 1)))]
    med = statistics.median(v)
    return f"p50 {med:.2f}  p95 {p95:.2f}  min {v[0]:.2f}  max {v[-1]:.2f}  (n={len(v)})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", type=Path)
    args = ap.parse_args(argv)
    rows = [json.loads(line) for line in (args.session / "turns.jsonl").read_text().splitlines()]
    c = classify(rows)
    cued = len(c["hits"]) + c["missed"] + c["false_early"]
    quiet = c["quiet_ok"] + c["false_quiet"]
    print(f"{args.session.name}: {len(rows)} log records")
    print(
        f"- cued replies (talk over the beep): {cued}; interrupted after the beep: {len(c['hits'])}"
    )
    missed, early = c["missed"], c["false_early"]
    print(f"  played to the end (missed): {missed}; interrupted before the beep: {early}")
    print(
        f"- quiet replies (nobody talks): {quiet}; played to the end: {c['quiet_ok']}"
        f", interrupted (false trigger): {c['false_quiet']}"
    )
    print(
        f"- its own words heard back as speech and ignored by the text guard: {c['ignored_echo']}"
    )
    print(f"- stop phrases that only silenced it: {c['stop_words']}")
    print(f"- interruptions that carried a new question which was then answered: {c['pivots']}")
    print(f"- seconds from the beep to the stop: {stats(c['hits'])}")
    for key, label in (
        ("retrieval_ms", "retrieval ms"),
        ("tts_first_audio_ms", "tts first audio ms"),
        ("speech_end_to_first_audio_ms", "you stopped -> first answer audio ms (server side)"),
        ("stt_server_ms", "speech server ms"),
        ("pause_ms", "your pause before the turn ended ms"),
    ):
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        print(f"- {label}: {stats(vals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
