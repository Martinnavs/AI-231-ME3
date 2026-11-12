"""Score a live session against the scripted test in config/eval/live_test_script.json.

    .venv/bin/python scripts/live_test_report.py --print           # the script to read aloud
    .venv/bin/python scripts/live_test_report.py [--session DIR]   # score the newest stack session

Each scripted line is matched, in order, to the logged turn whose text is most similar (the
recogniser may mishear a word). ADDRESS lines should be answered; SIDE lines (said to someone
else) should be ignored; every logged turn that matches no line is background the microphone
picked up, and should be ignored too. A turn counts as ignored when the loudness gate or the
addressee gate dropped it (no spoken answer). A line with no logged turn was never heard by the
microphone's speech detector: fine for SIDE, a miss for ADDRESS.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "config" / "eval" / "live_test_script.json"
MIN_SIMILARITY = 0.5
LOOKAHEAD = 12  # turns after the last match a line may be found in


def normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def status(turn: dict[str, Any]) -> str:
    """'answered', 'gate' (loudness gate dropped it), 'text' (addressee gate) or 'silent'."""
    gate = turn.get("level_gate") or {}
    if gate.get("far") and not turn.get("answer_words"):
        return "gate"
    if turn.get("addressee") == "ignore" and not turn.get("answer_words"):
        return "text"
    return "answered" if turn.get("answer_words") else "silent"


def match_lines(lines: list[dict[str, Any]], turns: list[dict[str, Any]]) -> dict[str, int | None]:
    """Line id -> index of the turn it was found in (None: no turn)."""
    found: dict[str, int | None] = {}
    last = -1
    for line in lines:
        want = normalise(line["say"])
        best, best_score = None, MIN_SIMILARITY
        for i in range(last + 1, min(last + 1 + LOOKAHEAD, len(turns))):
            score = difflib.SequenceMatcher(
                None, want, normalise(turns[i].get("question", ""))
            ).ratio()
            if score > best_score:
                best, best_score = i, score
        found[line["id"]] = best
        if best is not None:
            last = best
    return found


def evaluate(script: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [line | {"block": b["id"]} for b in script["blocks"] for line in b["lines"]]
    found = match_lines(lines, turns)
    matched = {i for i in found.values() if i is not None}
    owner: dict[int, str] = {}
    current = None
    block_of = {line["id"]: line["block"] for line in lines}
    by_index = {i: lid for lid, i in found.items() if i is not None}
    for i in range(len(turns)):
        if i in by_index:
            current = block_of[by_index[i]]
        owner[i] = current or "before"
    blocks: dict[str, dict[str, Any]] = {
        b["id"]: {"title": b["title"], "address": [], "side": [], "background": []}
        for b in script["blocks"]
    }
    for line in lines:
        i = found[line["id"]]
        st = "not heard" if i is None else status(turns[i])
        row = {"line": line, "turn": None if i is None else turns[i], "status": st}
        blocks[line["block"]]["address" if line["kind"] == "ADDRESS" else "side"].append(row)
    for i, turn in enumerate(turns):
        if i not in matched and owner[i] in blocks:
            blocks[owner[i]]["background"].append({"turn": turn, "status": status(turn)})
    return blocks


def gate_note(turn: dict[str, Any] | None) -> str:
    gate = (turn or {}).get("level_gate") or {}
    if gate.get("turn_db") is None:
        return ""
    return f" [level {gate['turn_db']} vs your {gate['reference_db']} dBFS]"


def report(blocks: dict[str, dict[str, Any]]) -> str:
    out: list[str] = []
    totals = {"addr": 0, "addr_ok": 0, "side": 0, "side_ok": 0, "bg": 0, "bg_bad": 0}
    problems: list[str] = []
    for bid, b in blocks.items():
        a_ok = sum(r["status"] == "answered" for r in b["address"])
        s_ok = sum(r["status"] != "answered" for r in b["side"])
        bg_bad = sum(r["status"] == "answered" for r in b["background"])
        out.append(
            f"{bid}  {b['title']:<34} addressed answered {a_ok}/{len(b['address'])}   "
            f"side ignored {s_ok}/{len(b['side'])}   background heard {len(b['background'])}, "
            f"answered {bg_bad}"
        )
        totals["addr"] += len(b["address"])
        totals["addr_ok"] += a_ok
        totals["side"] += len(b["side"])
        totals["side_ok"] += s_ok
        totals["bg"] += len(b["background"])
        totals["bg_bad"] += bg_bad
        for r in b["address"]:
            if r["status"] != "answered":
                problems.append(
                    f"  {r['line']['id']} should have been ANSWERED, was {r['status']}: "
                    f'"{r["line"]["say"]}"{gate_note(r["turn"])}'
                )
        for r in b["side"]:
            if r["status"] == "answered":
                problems.append(
                    f'  {r["line"]["id"]} was side talk but ANSWERED: "{r["line"]["say"]}"'
                )
        for r in b["background"]:
            if r["status"] == "answered":
                text = r["turn"].get("question", "")[:70]
                problems.append(f'  {bid} background ANSWERED: "{text}"{gate_note(r["turn"])}')
    out.append("")
    out.append(
        f"ALL  addressed answered {totals['addr_ok']}/{totals['addr']}   "
        f"side ignored {totals['side_ok']}/{totals['side']}   "
        f"background answered {totals['bg_bad']} of {totals['bg']} turns picked up"
    )
    if problems:
        out += ["", "To look at:", *problems]
    return "\n".join(out)


def newest_session() -> Path:
    sessions = sorted((ROOT / "out" / "sessions").glob("stack-*"), key=lambda p: p.stat().st_mtime)
    if not sessions:
        sys.exit("no stack session found under out/sessions")
    return sessions[-1]


def print_script(script: dict[str, Any]) -> None:
    print(script["about"], "\n")
    for b in script["blocks"]:
        print(f"== Block {b['id']}: {b['title']}\n   Set up: {b['setup']}")
        for line in b["lines"]:
            tag = "say to it " if line["kind"] == "ADDRESS" else "to someone else"
            print(f"   {line['id']:<3} [{tag}] {line['say']}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", dest="show")
    parser.add_argument("--session", type=Path, default=None)
    parser.add_argument("--script", type=Path, default=SCRIPT)
    parser.add_argument("--since-turn", type=int, default=0, help="ignore turns before this one")
    args = parser.parse_args()
    script = json.loads(args.script.read_text())
    if args.show:
        print_script(script)
        return
    session = args.session or newest_session()
    turns = []
    for line in (session / "turns.jsonl").read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row.get("question"), str) and row.get("turn", 0) >= args.since_turn:
            turns.append(row)
    print(f"session {session.name}: {len(turns)} turns\n")
    print(report(evaluate(script, turns)))


if __name__ == "__main__":
    main()
