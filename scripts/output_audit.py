"""Audit what the agent said, from the agent server's logs (pipeline/out/agent_server/*.jsonl).

    .venv/bin/python scripts/output_audit.py [log.jsonl ...]   # default: both checkouts' logs

Free, no network. One line per check with the number of turns (and the examples):
  repeated_sentence  two first-person status sentences of one reply restate each other
  premature_claim    "I have updated/added/created ..." in a turn that only started a job
  duplicate_job      two delegate calls within DUPLICATE_WINDOW_S
  fragment_tool      a 1-2 word turn that started a lookup or a job ("Boss." -> a web search)
The same checks, run on a fixed set of logs before and after a change, are the regression test of
what the listener hears (2026-11-12: ten redundant or buggy outputs in two sessions).
"""

from __future__ import annotations

import glob
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOTS = (
    Path("/mnt/School/PhD/AI231/AI-231-ME3-agy/pipeline/out/agent_server"),
    Path("/mnt/School/PhD/AI231/AI-231-ME3/pipeline/out/agent_server"),
)
DUPLICATE_WINDOW_S = 25.0
OVERLAP = 0.34  # share of the shorter sentence's content words that the other has
# a sentence in the assistant's own voice about its own action ("I am updating ...", "OK, I am
# ...", "It's currently being processed ..."): two of these in one reply restate each other. A
# sentence that names the topic again before its answer ("Looking up X. X was ...") is natural.
AGENT_VOICE = re.compile(
    r"^(?:ok(?:ay)?[,.]?\s+|sure[,.]?\s+|alright[,.]?\s+)?(?:i(?:'m|'ve| am| have| will|'ll)\b"
    r"|it(?:'s| is) (?:currently|being)|the (?:task|update|job|website|page)\b)",
    re.IGNORECASE,
)
MIN_WORDS = 3  # content words a sentence needs to be compared
_STOP_TEXT = (
    "a an the and or of to in on for with is are was were be i you it this that my your we am "
    "will would can could have has had do does did not no yes ok okay now then just also so as at "
    "by from up out about into over its me us them they he she there here what which who when"
)
STOP = frozenset(re.findall(r"[a-z]+", _STOP_TEXT))
PREMATURE = re.compile(
    r"\bI(?:'ve| have)\s+(?:just\s+)?(?:updated|added|created|built|finished|made|changed)\b",
    re.IGNORECASE,
)
LOOKUPS = frozenset({"web_answer", "web_research", "search_docs", "delegate"})


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 2}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def repeated(text: str) -> list[tuple[str, str]]:
    """Pairs of sentences of one reply that restate each other."""
    out: list[tuple[str, str]] = []
    parts = [p for p in sentences(text) if AGENT_VOICE.match(p)]
    for i, a in enumerate(parts):
        for b in parts[i + 1 :]:
            wa, wb = words(a), words(b)
            small = min(len(wa), len(wb))
            if small >= MIN_WORDS and len(wa & wb) / small >= OVERLAP:
                out.append((a, b))
    return out


def seconds(stamp: str) -> float:
    h, m, s = (int(x) for x in stamp.split(":"))
    return h * 3600 + m * 60 + s


def audit(rows: Sequence[dict[str, Any]]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {
        "repeated_sentence": [],
        "premature_claim": [],
        "duplicate_job": [],
        "fragment_tool": [],
    }
    last_job = -1e9
    for row in rows:
        text = str(row.get("text", ""))
        tools = [str(c.get("tool", "")) for c in row.get("calls", [])]
        said = f"{row.get('time', '')} {text[:90]!r}"
        if repeated(text):
            found["repeated_sentence"].append(said)
        if "delegate" in tools and PREMATURE.search(text):
            found["premature_claim"].append(said)
        if "delegate" in tools:
            now = seconds(str(row["time"])) if row.get("time") else 0.0
            if now - last_job <= DUPLICATE_WINDOW_S:
                found["duplicate_job"].append(said)
            last_job = now
        if len(str(row.get("question", "")).split()) <= 2 and LOOKUPS & set(tools):
            found["fragment_tool"].append(f"{row['question']!r} -> {sorted(LOOKUPS & set(tools))}")
    return found


def load(paths: Sequence[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths = args or sorted(p for root in ROOTS for p in glob.glob(str(root / "*.jsonl")))
    rows = load(paths)
    found = audit(rows)
    print(f"{len(rows)} turns in {len(paths)} logs")
    for name, hits in found.items():
        print(f"{name:<18}{len(hits):>4}")
        for hit in hits[:3]:
            print(f"    {hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
