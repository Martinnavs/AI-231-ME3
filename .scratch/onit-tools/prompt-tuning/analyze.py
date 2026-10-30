"""Score the interleaved dry runs of P0/P1/P2 with the metrics fixed in PLAN.md.

    cd pipeline && python3 ../.scratch/onit-tools/prompt-tuning/analyze.py
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

OUT = Path("out/agent_dryrun")
CONDS = ("P0", "P1", "P2")
PACKS = {"m": "87-pack", "d": "delegate pack", "r": "recheck pack", "h": "held-out pack"}
ALIAS = {"wiki_summary": "web_answer"}


def key(call: dict[str, Any]) -> tuple[str, str | None]:
    if "tool" in call:
        return f"tool:{ALIAS.get(call['tool'], call['tool'])}", None
    slot = call.get("slot")
    return str(call.get("intent")), str(slot).lower() if slot else None


def matches(calls: list[dict[str, Any]], expect: list[dict[str, Any]]) -> bool:
    """Exact multiset match; unlike the dry run, repeated calls to one tool are not collapsed."""
    got, want = Counter(key(c) for c in calls), Counter(key(e) for e in expect)
    if got == want:
        return True
    for k in [k for k in want if k[0] == "CREATE_REMINDER"]:
        found = next((g for g in got if g[0] == k[0] and k[1] and g[1] and (k[1] in g[1] or g[1] in k[1])), None)
        if found is not None:
            got[k] += got.pop(found)
    return got == want


PREFIX = ""


def load(run: str) -> list[dict[str, Any]]:
    path = OUT / (PREFIX + run) / "rows.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def pct(values: list[float], q: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1] if len(values) > 1 else float("nan")


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["status"] == "ok"]
    exact = sum(matches(r["calls"], r["expect"]) for r in ok)
    extra = sum(max(0, len(r["calls"]) - len(r["expect"])) for r in ok)
    false = sum(1 for r in ok if not r["expect"] and r["calls"])
    delegated = [r for r in ok if any(e.get("tool") == "delegate" for e in r["expect"])]
    negatives = [r for r in ok if not any(str(e.get("tool", "")).startswith("delegate") for e in r["expect"])]
    return {
        "n": len(ok), "bad": len(rows) - len(ok), "exact": exact, "extra": extra, "false": false,
        "recall": sum(any(c.get("tool") == "delegate" for c in r["calls"]) for r in delegated), "recall_n": len(delegated),
        "stray": sum(any(str(c.get("tool", "")).startswith("delegate") for c in r["calls"]) for r in negatives),
    }


def main() -> None:
    import sys

    global PREFIX
    only = sys.argv[1] if len(sys.argv) > 1 else "mdr"
    PREFIX = sys.argv[2] if len(sys.argv) > 2 else ""
    packs = {p: label for p, label in PACKS.items() if p in only}
    conds = tuple(c for c in CONDS if any(load(f"{c}_{p}{n}") for p in packs for n in (1, 2, 3)))
    table: dict[str, dict[str, list[dict[str, Any]]]] = {c: {p: [] for p in packs} for c in conds}
    rows_by: dict[str, list[dict[str, Any]]] = {c: [] for c in conds}
    for c in conds:
        for p in packs:
            for n in (1, 2, 3):
                rows = load(f"{c}_{p}{n}")
                if rows:
                    table[c][p].append(score(rows))
                    rows_by[c].extend(rows)
    for p, label in packs.items():
        print(f"\n== {label}: per repetition (alias-exact / extra calls / false calls / bad)  [n turns]")
        for c in conds:
            runs = table[c][p]
            cells = "  ".join(f"{s['exact']}/{s['extra']}/{s['false']}/{s['bad']}" for s in runs)

            def mean(k: str, runs: list[dict[str, Any]] = runs) -> float:
                return sum(s[k] for s in runs) / len(runs) if runs else float("nan")

            print(f"  {c}: {cells:<28} mean exact {mean('exact'):5.1f}  extra {mean('extra'):4.1f}  false {mean('false'):4.1f}  [{runs[0]['n'] if runs else 0}]")
    if "d" in packs:
        print("\n== delegate pack: delegations made when expected, and delegate* calls on turns that should not have one")
        for c in conds:
            runs = table[c]["d"]
            print(f"  {c}: recall {[str(s['recall']) + '/' + str(s['recall_n']) for s in runs]}  stray {[s['stray'] for s in runs]}")
    print("\n== delegate* calls on the other packs (must be 0)")
    for c in conds:
        print(f"  {c}: {sum(s['stray'] for p in packs if p != 'd' for s in table[c][p])}")
    print("\n== latency over every ok turn of every run (seconds)")
    print(f"  {'':<4}{'tool first-text p50':>21}{'p90':>7}{'no-tool p50':>14}{'p90':>7}{'first call p50':>16}   n(tool/none)")
    base: dict[str, float] = {}
    for c in conds:
        ok = [r for r in rows_by[c] if r["status"] == "ok"]
        tool = [r["first_text_s"] for r in ok if r["calls"] and r["first_text_s"]]
        none = [r["first_text_s"] for r in ok if not r["calls"] and r["first_text_s"]]
        call = [r["first_call_s"] for r in ok if r["calls"] and r["first_call_s"]]
        vals = (statistics.median(tool), pct(tool, 90), statistics.median(none), pct(none, 90), statistics.median(call))
        print(f"  {c:<4}{vals[0]:>21.2f}{vals[1]:>7.2f}{vals[2]:>14.2f}{vals[3]:>7.2f}{vals[4]:>16.2f}   {len(tool)}/{len(none)}")
        if c == "P0":
            base = {"tool": vals[0], "none": vals[2]}
        elif base:
            print(f"      vs P0: tool p50 {100 * (vals[0] / base['tool'] - 1):+.1f}%  no-tool p50 {100 * (vals[2] / base['none'] - 1):+.1f}%")
    if "h" in packs:
        print("\n== held-out pack by category: alias-exact turns out of 3 repetitions x prompts (P0 | P1)")
        kinds = sorted({r["kind"] for r in rows_by[conds[0]]})
        for kind in kinds:
            cells = []
            for c in conds:
                rs = [r for r in rows_by[c] if r["kind"] == kind and r["status"] == "ok"]
                cells.append(f"{sum(matches(r['calls'], r['expect']) for r in rs)}/{len(rs)}")
            print(f"  {kind:<18}", "  ".join(f"{c}={x}" for c, x in zip(conds, cells, strict=True)))
        print("\n== held-out prompts where the conditions differ (exact count of 3)")
        ids = sorted({r["id"] for r in rows_by[conds[0]]})
        for i in ids:
            counts = [sum(matches(r["calls"], r["expect"]) for r in rows_by[c] if r["id"] == i and r["status"] == "ok") for c in conds]
            if len(set(counts)) > 1:
                sample = next(r for r in rows_by[conds[-1]] if r["id"] == i)
                got = [[x.get("tool") or x.get("intent") for x in r["calls"]] for r in rows_by[conds[-1]] if r["id"] == i]
                print(f"  {i} {sample['turns'] if 'turns' in sample else ''}{dict(zip(conds, counts, strict=True))} last-cond calls: {got}")


if __name__ == "__main__":
    main()
