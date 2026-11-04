# ruff: noqa: E501  (report text and the docstring read better unwrapped)
"""Score a spoken session against stress-batch-v2.jsonl.

    python3 scripts/score_stress.py [out/sessions/<session>] [agent_server log jsonl]

With no arguments it takes the newest session and the newest agent-server log. Your turns are matched to
the script by wording alone (best text match over the whole script, threshold 0.6, each turn and line used
once, ties by order), so out-of-order and repeated lines score. The recogniser mishears things, so the
match is fuzzy; turns that match nothing are listed as extras with their route and reply, and script lines
never matched are "not run". Per item it checks the route
(router / Gemini / fast path), the tools called, words in the spoken reply, the time to first sound, and
for interruptions the cut-off time and what happened next.
"""

from __future__ import annotations

import difflib
import glob
import json
import os
import re
import statistics as st
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(os.environ.get("STRESS_SCRIPT") or ROOT / "docs/stress/stress-batch-v2.jsonl")


def norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = {"thousand": 1000, "million": 1_000_000}
# greetings and thanks may be repeated back (the same set as call_policy.SOCIAL)
SOCIAL = frozenset(
    [
        "hello",
        "hi",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "good night",
        "goodbye",
        "bye",
        "thanks",
        "thank you",
    ]
)


def _number_value(words: list[str]) -> int | None:
    """ "one hundred sixty eight" -> 168; None when the words are not one number."""
    total, cur, seen = 0, 0, False
    for w in words:
        if w == "and":
            continue
        if w in _ONES:
            if cur % 100 and not (cur % 10 == 0 and cur % 100 >= 20 and 0 < _ONES.index(w) < 10):
                return None
            cur += _ONES.index(w)
        elif w in _TENS:
            if cur % 100:
                return None
            cur += 10 * (_TENS.index(w) + 2)
        elif w == "hundred" and seen:
            cur = (cur or 1) * 100 if cur < 100 else cur * 100
        elif w in _SCALES and seen:
            total += (cur or 1) * _SCALES[w]
            cur = 0
        else:
            return None
        seen = True
    return total + cur if seen else None


def digits_for_words(text: str) -> str:
    """Spoken numbers written as digits ("one hundred sixty-eight" -> "168"); other words are kept."""
    tokens = re.findall(r"[A-Za-z']+|\d[\d,.:]*|[^\sA-Za-z\d]", text.replace("-", " "))
    out: list[str] = []
    i = 0
    numeric = {*_ONES, *_TENS, "hundred", *_SCALES}
    while i < len(tokens):
        if tokens[i].lower() in numeric:
            j, best, best_j = i, None, i
            while j < len(tokens) and tokens[j].lower() in numeric | {"and"}:
                j += 1
                if tokens[j - 1].lower() != "and":
                    v = _number_value([t.lower() for t in tokens[i:j]])
                    if v is not None:
                        best, best_j = v, j
            if best is not None:
                out.append(str(best))
                i = best_j
                continue
        out.append(tokens[i])
        i += 1
    return " ".join(out)


def match_turns(
    questions: list[str], lines: list[str], threshold: float = 0.6
) -> tuple[dict[int, int], list[int]]:
    """Turn index -> line index by best text match over all lines; each used once, ties by order."""
    pairs = []
    for i, q in enumerate(questions):
        for j, line in enumerate(lines):
            s = ratio(q, line)
            if s >= threshold:
                pairs.append((-s, j, i))
    pairs.sort()
    turn_of: dict[int, int] = {}
    used: set[int] = set()
    for _, j, i in pairs:
        if j not in turn_of and i not in used:
            turn_of[j] = i
            used.add(i)
    return turn_of, [i for i in range(len(questions)) if i not in used]


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def route_of(a: dict[str, Any] | None) -> str:
    if a is None:
        return "unknown"
    if a.get("fast_path"):
        return "fast"
    if a.get("routed") or a.get("hedge_winner") == "router":
        return "router"
    return "gemini"


def call_names(a: dict[str, Any] | None) -> tuple[set[str], int]:
    names: set[str] = set()
    n = 0
    for c in (a or {}).get("calls") or []:
        n += 1
        args = c.get("args") or {}
        for v in (c.get("tool"), c.get("intent"), args.get("intent")):
            if v:
                names.add(str(v))
    return names, n


def p90(x: list[float]) -> float:
    x = sorted(x)
    return x[int(0.9 * (len(x) - 1))]


def main() -> int:
    newest_session = sorted(
        glob.glob(str(ROOT / "out/sessions/*/turns.jsonl")), key=lambda f: Path(f).stat().st_mtime
    )[-1]
    newest_log = sorted(glob.glob(str(ROOT / "pipeline/out/agent_server/*.jsonl")))[-1]
    sess_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(newest_session).parent
    agent_log = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(newest_log)
    items = load(SCRIPT)
    rows = load(sess_dir / "turns.jsonl")
    agent = [a for a in load(agent_log) if a.get("question") != "Say OK."]
    print(
        f"session {sess_dir.name}: {len(rows)} turns; agent log {agent_log.name}: {len(agent)} requests; script: {len(items)} items\n"
    )

    # the script as a sequence of lines to hear: each item, then the words said over an interruption
    seq: list[tuple[int, str, str]] = []
    for it in items:
        if it.get("skip"):
            continue  # out of scope (the reason is in the script): not said, not scored
        say = re.sub(r"\[[^\]]*\]|\.\.\.", " ", it["say"])
        seq.append((it["id"], "main", say))
        if it.get("interrupt"):
            seq.append((it["id"], "follow", it["interrupt"]["say"]))

    turn_of, extras = match_turns([r.get("question") or "" for r in rows], [x[2] for x in seq])
    match = {(seq[j][0], seq[j][1]): i for j, i in turn_of.items()}

    # agent-server request for each session row: matched from the end backwards, so requests that came
    # before the session (warm-ups, test questions) cannot be paired with it
    a_of: dict[int, dict[str, Any]] = {}
    k = len(agent) - 1
    for i in range(len(rows) - 1, -1, -1):
        for j in range(k, max(k - 6, -1), -1):
            if ratio(rows[i].get("question") or "", agent[j].get("question") or "") >= 0.6:
                a_of[i] = agent[j]
                k = j - 1
                break

    results: list[tuple[int, str, str, str]] = []  # (id, verdict, say, note)
    first_sound: dict[int, float] = {}
    by_route: dict[str, list[float]] = {}
    for it in items:
        iid = it["id"]
        i = match.get((iid, "main"))
        label = it["say"][:46]
        if it.get("skip"):
            results.append((iid, "SKIP", label, it["skip"]))
            continue
        if i is None:
            results.append((iid, "NOT RUN", label, ""))
            continue
        r, a = rows[i], a_of.get(i)
        notes: list[str] = []
        fails: list[str] = []
        names, ncalls = call_names(a)
        text = (a or {}).get("text") or ""
        snd = r.get("speech_end_to_first_audio_ms")
        words = r.get("answer_words") or 0
        route = route_of(a)
        if snd and not r.get("interrupted"):
            first_sound[iid] = snd
            by_route.setdefault(route, []).append(snd)
        notes.append(f"{route}" + (f" {snd / 1000:.2f}s" if snd else ""))
        if (
            it.get("route") in ("router", "fast", "gemini")
            and route != it["route"]
            and not (it["route"] == "router" and route == "fast")
        ):
            fails.append(f"route {route}, wanted {it['route']}")
        if it.get("intents_any") and not set(it["intents_any"]) & names:
            fails.append(f"no {'/'.join(it['intents_any'])} call (saw {sorted(names) or 'none'})")
        if it.get("tools_any") and not set(it["tools_any"]) & names:
            fails.append(f"no {'/'.join(it['tools_any'])} call (saw {sorted(names) or 'none'})")
        if it.get("tools_forbid") and set(it["tools_forbid"]) & names:
            fails.append(f"called {sorted(set(it['tools_forbid']) & names)}")
        if it.get("min_calls") and ncalls < it["min_calls"]:
            fails.append(f"{ncalls} calls, wanted {it['min_calls']}")
        race = (a or {}).get("race") or {}
        if race:
            notes.append(
                f"race {race.get('winner')} (docs {race.get('docs')}, {race.get('doc_s')}s)"
            )
        if it.get("race_winner") and race.get("winner") != it["race_winner"]:
            fails.append(f"race winner {race.get('winner')}, wanted {it['race_winner']}")
        text_num = digits_for_words(text)
        if (
            it.get("text_re")
            and not re.search(it["text_re"], text)
            and not re.search(it["text_re"], text_num)
        ):
            fails.append(f"reply lacks /{it['text_re']}/")
        if it.get("text_not_re") and re.search(it["text_not_re"], text):
            fails.append(f"reply matches /{it['text_not_re']}/")
        if it.get("min_words") and words < it["min_words"]:
            fails.append(f"{words} words, wanted {it['min_words']}+")
        heard, said = norm(r.get("question") or ""), norm(text)
        if (
            len(heard.split()) >= 3
            and heard not in SOCIAL
            and said
            and difflib.SequenceMatcher(None, heard, said).ratio() >= 0.8
        ):
            fails.append("PARROTED the (garbled) turn back as its reply")
        if it.get("max_words") and words > it["max_words"]:
            fails.append(f"{words} words, wanted at most {it['max_words']}")
        if it.get("max_ms") and snd and snd > it["max_ms"]:
            fails.append(f"slow {snd / 1000:.2f}s > {it['max_ms'] / 1000:.1f}s")
        if it.get("faster_than") and snd and first_sound.get(it["faster_than"]):
            if snd >= first_sound[it["faster_than"]] - 300:
                fails.append(
                    f"not faster than item {it['faster_than']} ({first_sound[it['faster_than']] / 1000:.2f}s)"
                )
            else:
                notes.append(
                    f"cache: {first_sound[it['faster_than']] / 1000:.2f}s -> {snd / 1000:.2f}s"
                )
        if it.get("silent"):
            if r.get("interrupted") or r.get("ignored_echo"):
                fails.append(f"ECHO FALSE TRIGGER ({r.get('interrupted') or 'ignored_echo'})")
            else:
                notes.append("not interrupted")
        if it.get("interrupt"):
            fi = match.get((iid, "follow"))
            if r.get("interrupted") != "barge_in":
                fails.append(f"interrupt not registered ({r.get('interrupted')})")
            else:
                cut = r.get("cancel_to_silence_ms")
                notes.append(f"cut in {cut:.1f} ms" if cut is not None else "cut")
                if cut is not None and cut > 100:
                    fails.append(f"cut-off {cut:.0f} ms")
            if fi is None:
                fails.append("follow-up never heard")
            else:
                fr, fa = rows[fi], a_of.get(fi)
                fnames, _ = call_names(fa)
                if it["interrupt"].get("kind") == "stop":
                    if (fr.get("answer_words") or 0) > 0:
                        fails.append("said something after the stop word")
                    else:
                        notes.append("stop word: silent")
                if it.get("followup_intents") and not set(it["followup_intents"]) & (
                    fnames
                    | ({"LIGHT_ON"} if "light" in norm((fa or {}).get("text", "")) else set())
                ):
                    fails.append(
                        f"follow-up did not {'/'.join(it['followup_intents'])} (saw {sorted(fnames) or 'none'})"
                    )
                if it.get("followup_tools") and not set(it["followup_tools"]) & fnames:
                    fails.append(
                        f"follow-up did not call {'/'.join(it['followup_tools'])} (saw {sorted(fnames) or 'none'})"
                    )
        if it.get("once"):
            dups = [
                j
                for j in extras
                if ratio(rows[j].get("question") or "", it["say"].split("...")[-1]) >= 0.6
            ]
            if dups:
                fails.append(f"ran twice ({1 + len(dups)} turns)")
        if r.get("fillers"):
            notes.append(f"fillers {len(r['fillers'])}")
        verdict = "FAIL" if fails else "PASS"
        if fails and it.get("known_issue"):
            verdict = "FAIL*"  # a known issue that is still there
        if not fails and it.get("known_issue"):
            verdict = "PASS*"  # a known issue that is gone
        results.append((iid, verdict, label, "; ".join(fails) if fails else ", ".join(notes)))

    print(f"{'#':>2} {'':7} {'say':46}  result")
    for iid, v, label, note in results:
        print(f"{iid:>2} {v:7} {label:46}  {note}")
    c = {
        v: sum(1 for x in results if x[1] == v)
        for v in ("PASS", "PASS*", "FAIL", "FAIL*", "NOT RUN", "SKIP")
    }
    print(
        f"\n{c['PASS']} pass, {c['PASS*']} known issues now fixed, {c['FAIL']} fail, {c['FAIL*']} known issues still present, {c['NOT RUN']} not run, {c['SKIP']} skipped  (of {len(results)})"
    )
    print("  (* = a known issue from earlier runs)")

    if extras:
        print("\nturns that matched no script line (repeats, retries, mis-hearings):")
        for j in extras:
            r = rows[j]
            a = a_of.get(j)
            print(
                f"   turn {r.get('turn')}: {(r.get('question') or '')[:60]!r}  {route_of(a) if a else 'no agent row'}"
                f"  interrupted={r.get('interrupted')} words={r.get('answer_words')}"
                f"  reply: {((a or {}).get('text') or '')[:90]!r}"
            )

    print("\nlatency, end of speech to first sound (non-interrupted, answered turns):")
    allv = [v for vs in by_route.values() for v in vs]
    if allv:
        print(
            f"   all      n={len(allv):2d} median {st.median(allv) / 1000:.2f}s p90 {p90(allv) / 1000:.2f}s max {max(allv) / 1000:.2f}s"
        )
    for rt, vs in sorted(by_route.items()):
        print(
            f"   {rt:8} n={len(vs):2d} median {st.median(vs) / 1000:.2f}s p90 {p90(vs) / 1000:.2f}s max {max(vs) / 1000:.2f}s"
        )
    tts = [r["tts_first_audio_ms"] for r in rows if r.get("tts_first_audio_ms")]
    cuts = [
        r["cancel_to_silence_ms"]
        for r in rows
        if r.get("interrupted") == "barge_in" and r.get("cancel_to_silence_ms") is not None
    ]
    if tts:
        print(
            f"   TTS first audio median {st.median(tts):.0f} ms p90 {p90(tts):.0f} ms; underruns {sum(r.get('audio_underruns') or 0 for r in rows)}; fillers played on {sum(1 for r in rows if r.get('fillers'))} turns"
        )
    if cuts:
        print(
            f"   barge-in cut-off: {len(cuts)} interruptions, median {st.median(cuts):.1f} ms, max {max(cuts):.1f} ms"
        )
    echo = sum(1 for r in rows if r.get("ignored_echo"))
    print(f"   its own words heard back as speech (ignored_echo): {echo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
