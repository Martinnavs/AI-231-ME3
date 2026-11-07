"""Offline replay of the misroute guards over the logged agent turns (no network, no model).

Run from pipeline/: uv run python ../scripts/misroute_replay.py [log-glob] > out.txt
Router turns go through call_policy.decide with the own-work rule off and on; logged open_app and
search_docs calls go through the old and the new checks. Every changed decision is printed.
"""

import glob
import json
import re
import sys
from pathlib import Path

from voice_pipeline import agent_tools
from voice_pipeline import call_policy as cp
from voice_pipeline.router import spec

AVAILABLE = {"smart_home", "search_docs", "get_current_time", "convert_time", "weather",
             "wiki_summary", "web_answer", "get_volume", "set_volume", "device_status",
             "delegate", "delegate_status", "delegate_cancel", "open_app"}  # fmt: skip
OLD_OPEN = re.compile(
    r"\b(open|play|launch|start|run|show|yes|yeah|yep|sure|ok|okay|please|go ahead|do it)\b",
    re.IGNORECASE,
)
cp.known_apps = lambda: ["angry_birds", "flappy_bird"]  # type: ignore[assignment]
own_work = cp.about_own_work


def decide(on: bool, calls: list, user: str) -> str | None:
    cp.about_own_work = own_work if on else (lambda user: False)  # type: ignore[assignment]
    try:
        return cp.decide(calls, user, AVAILABLE).reason
    finally:
        cp.about_own_work = own_work  # type: ignore[assignment]


def main() -> None:
    pattern = sys.argv[1] if len(sys.argv) > 1 else "out/agent_server/*.jsonl"
    routed = opens = docs = 0
    changed: list[str] = []
    for f in sorted(glob.glob(pattern)):
        tag = Path(f).stem
        for line in Path(f).read_text().splitlines():
            r = json.loads(line)
            user = r.get("question") or ""
            if lines := r.get("router_lines"):
                try:
                    calls = spec.parse_output(lines)
                except Exception:
                    calls = None
                if calls:
                    routed += 1
                    old, new = decide(False, calls, user), decide(True, calls, user)
                    if old != new:
                        changed.append(
                            f"[{tag}] router {user!r}\n    {lines!r}: {old} -> {new}"
                            f"  (live routed={r.get('routed')})"
                        )
            for c in r.get("calls") or []:
                if c.get("tool") == "open_app":
                    opens += 1
                    old, new = bool(OLD_OPEN.search(user)), agent_tools.asks_to_open(user)
                    if old != new:
                        changed.append(
                            f"[{tag}] open_app {c.get('args')} {user!r}: opens {old} -> {new}"
                        )
                elif c.get("tool") == "search_docs":
                    docs += 1
                    if cp.about_own_work(user):
                        changed.append(f"[{tag}] search_docs {user!r}: runs -> skipped")
    print(
        f"{routed} router turns, {opens} open_app calls, {docs} search_docs calls replayed; "
        f"{len(changed)} decisions changed"
    )
    for c in changed:
        print(" ", c)


main()
