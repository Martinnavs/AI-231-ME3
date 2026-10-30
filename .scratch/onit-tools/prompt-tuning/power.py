"""How well would a ~100-prompt test have done? Re-sample the runs already made (no API calls).

For two conditions, take every prompt that was run, draw N of them, give each condition one randomly chosen
repetition of each drawn prompt (as if run once), and compare. Repeat many times.
    cd pipeline && python3 ../.scratch/onit-tools/prompt-tuning/power.py
"""

from __future__ import annotations

import importlib.util
import json
import random
import statistics
from pathlib import Path

spec = importlib.util.spec_from_file_location("analyze", Path(__file__).with_name("analyze.py"))
analyze = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(analyze)  # type: ignore[union-attr]
OUT = Path("out/agent_dryrun")


def outcomes(prefix: str, cond: str, packs: str, reps: tuple[int, ...]) -> dict[tuple[str, str, int], list[int]]:
    table: dict[tuple[str, str, int], list[int]] = {}
    for p in packs:
        for n in reps:
            path = OUT / f"{prefix}{cond}_{p}{n}" / "rows.jsonl"
            if not path.exists():
                continue
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            if any(r["status"] != "ok" for r in rows):
                continue
            for r in rows:
                table.setdefault((p, r["id"], r["turn"]), []).append(int(analyze.matches(r["calls"], r["expect"])))
    return table


def simulate(a: dict, b: dict, n: int, reps_each: int, sims: int = 4000) -> tuple[float, float, float, float]:
    keys = [k for k in a if k in b]
    rng = random.Random(1)
    diffs = []
    for _ in range(sims):
        pick = rng.sample(keys, min(n, len(keys)))
        da = sum(statistics.mean(rng.choice(a[k]) for _ in range(reps_each)) for k in pick)
        db = sum(statistics.mean(rng.choice(b[k]) for _ in range(reps_each)) for k in pick)
        diffs.append(db - da)
    full = sum(statistics.mean(b[k]) - statistics.mean(a[k]) for k in keys) / len(keys) * min(n, len(keys))
    return statistics.mean(diffs), statistics.stdev(diffs), sum(d > 0 for d in diffs) / sims, full


def report(title: str, a: dict, b: dict) -> None:
    keys = [k for k in a if k in b]
    print(f"\n{title}  ({len(keys)} prompts with data in both conditions)")
    print(f"  {'design':<34}{'mean diff':>10}{'sd':>7}{'P(says better)':>16}{'P(diff>=2 better)':>19}")
    for n, reps in ((100, 1), (100, 2), (60, 1), (180, 1)):
        n_eff = min(n, len(keys))
        a_, b_ = a, b
        mean, sd, pos, full = simulate(a_, b_, n_eff, reps)
        rng = random.Random(2)
        big = 0
        for _ in range(4000):
            pick = rng.sample(keys, n_eff)
            da = sum(statistics.mean(rng.choice(a[k]) for _ in range(reps)) for k in pick)
            db = sum(statistics.mean(rng.choice(b[k]) for _ in range(reps)) for k in pick)
            big += (db - da) >= 2
        print(f"  {n_eff} prompts x {reps} run{'s' if reps > 1 else ' '}{'':<20}{mean:>10.1f}{sd:>7.1f}{pos:>15.0%}{big / 4000:>19.0%}")
    print(f"  (the full data's difference scaled to 100 prompts: {full * 100 / min(100, len(keys)) * min(100, len(keys)) / min(100, len(keys)):.1f})")


report("Round 1: P1 (new prompt) against P0 (old)", outcomes("S", "P0", "mdrh", (1, 2, 3)), outcomes("S", "P1", "mdrh", (1, 2, 3)))
report("Round 2: P3 against P1", outcomes("T", "P1", "mdrg", (1, 2, 3)), outcomes("T", "P3", "mdrg", (1, 2, 3)))
