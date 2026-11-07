"""Free offline replay of the text addressee gate (doc_chat.addressee).

(a) false rejects over every real logged turn, (b) block rate on the benchmark clips,
(c) benchmark user-side texts that must not be ignored, (d) cost per decide() call.

The benchmark texts are the pool the rules were written while looking at (in-sample).
The logged real turns are the independent check.

    .venv/bin/python scripts/addressee_replay.py
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from doc_chat.addressee import decide as _decide  # noqa: E402

DECLARATIVES = "--declaratives" in sys.argv  # also run the opt-in declarative rule


def decide(text: str, **kw: object):  # type: ignore[no-untyped-def]
    return _decide(text, declaratives=DECLARATIVES, **kw)  # type: ignore[arg-type]


MANIFEST = ROOT / "fd-benchmark" / "datasets" / "manifest.json"


def logged_turns(bench: bool) -> list[str]:
    """Real logged turns; bench-* sessions replayed benchmark clips into the microphone."""
    out: list[str] = []
    for path in sorted((ROOT / "out" / "sessions").glob("*/turns.jsonl")):
        if path.parent.name.startswith("bench-") != bench:
            continue
        for line in path.read_text().splitlines():
            try:
                q = json.loads(line).get("question")
            except json.JSONDecodeError:
                continue
            if isinstance(q, str) and q.strip():
                out.append(q)
    return out


def false_rejects(turns: list[str], title: str) -> None:
    print(f"=== (a) FALSE REJECTS {title} ===")
    for since in (None, 3.0):
        hits = [t for t in turns if decide(t, since_reply_s=since, pending=False).label == "ignore"]
        labels: dict[str, int] = defaultdict(int)
        for t in turns:
            labels[decide(t, since_reply_s=since, pending=False).label] += 1
        pct = 100.0 * len(hits) / max(len(turns), 1)
        print(
            f"since_reply_s={since}: {len(hits)}/{len(turns)} ignored ({pct:.2f}%)  {dict(labels)}"
        )
        for t in hits:
            print(f"   IGNORED: {t!r}  {decide(t, since_reply_s=since, pending=False).reasons}")


def benchmark_texts() -> dict[str, list[str]]:
    groups = json.loads(MANIFEST.read_text())["groups"]
    out: dict[str, list[str]] = defaultdict(list)
    for clip in groups["false_tt"]["clips"]:
        meta = clip["meta"]
        if meta["kind"] in ("side_talk", "background", "tv_radio") and meta.get("text"):
            out[f"false_tt/{meta['kind']}"].append(meta["text"])
    for name in ("v15_talking_to_other", "v15_background"):
        for clip in groups[name]["clips"]:
            if clip["meta"].get("overlap_text"):
                out[name].append(clip["meta"]["overlap_text"])
    return out


def block_rates(groups: dict[str, list[str]]) -> None:
    print(
        "\n=== (b) BLOCK RATE on benchmark clips "
        "(IN-SAMPLE: same pool the rules were written near) ==="
    )
    for name, texts in groups.items():
        verdicts = [(t, decide(t, since_reply_s=None, pending=False)) for t in texts]
        blocked = [(t, v) for t, v in verdicts if v.label == "ignore"]
        print(f"{name}: blocked {len(blocked)}/{len(texts)}")
        for t, v in verdicts:
            tag = "BLOCKED" if v.label == "ignore" else f"LEAKED[{v.label}]"
            print(f"   {tag}: {t!r}  {v.reasons}")


def user_side() -> None:
    print("\n=== (c) benchmark user-side texts (real requests; must not be ignored) ===")
    groups = json.loads(MANIFEST.read_text())["groups"]
    texts: list[str] = []
    for clip in groups["v15_interruption"]["clips"]:
        if clip["meta"].get("current_turn_text"):
            texts.append(clip["meta"]["current_turn_text"])
    for name, group in groups.items():
        if name.startswith("v15"):
            texts += [
                c["meta"]["context_text"] for c in group["clips"] if c["meta"].get("context_text")
            ]
    bad = [t for t in texts if decide(t, since_reply_s=None, pending=False).label == "ignore"]
    print(f"ignored {len(bad)}/{len(texts)}")
    for t in bad:
        print(f"   IGNORED: {t!r}  {decide(t, since_reply_s=None, pending=False).reasons}")


def timing(sample: list[str]) -> None:
    print("\n=== (d) time per decide() call ===")
    times: list[float] = []
    n = len(sample)
    for i in range(10_000):
        t = sample[i % n]
        t0 = time.perf_counter()
        decide(t, since_reply_s=None, pending=False)
        times.append((time.perf_counter() - t0) * 1e6)
    times.sort()
    print(
        f"median {statistics.median(times):.1f} us, "
        f"p99 {times[int(0.99 * len(times))]:.1f} us (10000 calls)"
    )


def main() -> None:
    turns = logged_turns(bench=False)
    bench_turns = logged_turns(bench=True)
    groups = benchmark_texts()
    false_rejects(turns, "over real logged turns, bench-* sessions excluded (independent check)")
    print()
    false_rejects(
        bench_turns, "over bench-* sessions (benchmark clips played into the mic: NOT user turns)"
    )
    block_rates(groups)
    user_side()
    timing(turns + [t for g in groups.values() for t in g])


if __name__ == "__main__":
    main()
