"""Write the step-2 tickets from edits_step2.py and skeleton/ (verbatim code, exact line caps).

    python3 .scratch/echo-next/gen_tickets.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location("edits", HERE / "edits_step2.py")
assert spec and spec.loader
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)

UV = "uv run --offline --group laptop"
FENCE = "````"

TICKETS = [
    {
        "n": "01",
        "slug": "stop-pause",
        "title": '"pause" is an order, not a stop word, in the pivot strip',
        "why": 'Today `_strip_stop_prefix("Stop. Pause the music.")` returns "The music." (and '
        '"Wait, pause the video please" returns "The video please"): "pause" is in the stop-word '
        "prefix, so the order to pause is cut off with the stop. Found in "
        "`.scratch/echo-next/PLAN.md` E3. A bare \"pause\" still only silences the assistant "
        "(`_is_stop_phrase`, unchanged).",
        "edits": [("src/doc_chat/__main__.py", E.T01_MAIN)],
        "tests": ["tests/doc_chat/test_stop_pause.py"],
        "regress": ["tests/doc_chat/test_bargein.py"],
        "non_goals": "no change to `_is_stop_phrase`, `_STOP_WORDS`, `_STOP_CORE` or any other "
        "rule; no other file.",
    },
    {
        "n": "02",
        "slug": "strip-echo-prefix",
        "title": "drop the assistant's own words from the front of an interruption "
        "(--strip-echo-prefix)",
        "why": "After a barge-in the recogniser gets 1.2 s of pre-roll, so the assistant's last "
        'words often come before the user\'s "stop" ("2005 border. Stop. Set a timer for ten '
        'minutes."). The stop rules then miss the stop and the language model gets the echo '
        "(spurious STOP commands, a second timer). Behind a flag, off by default: two helpers in "
        "`bargein.py` and their use right after the existing echo text guard in `run_mic`. "
        "Evaluated offline on the logged transcripts (`.scratch/echo-next/PLAN.md` E3).",
        "edits": [
            ("src/doc_chat/bargein.py", E.T02_BARGEIN),
            ("src/doc_chat/__main__.py", E.T02_MAIN),
        ],
        "tests": ["tests/doc_chat/test_echo_prefix.py"],
        "regress": ["tests/doc_chat/test_bargein.py"],
        "non_goals": "no change to `looks_like_echo`, the stop rules or the pre-roll; nothing "
        "happens without `--strip-echo-prefix`; no other file.",
    },
    {
        "n": "03",
        "slug": "aec-lead-zero",
        "title": "no reference lead by default in the echo canceller",
        "why": "WebRTC AEC3 resets its render buffer when the reference stays more than 32 ms ahead "
        "of the microphone for a second, and finds render-to-echo delays only up to about 0.5 s. "
        "The 50 ms lead triggers the reset and uses up range; offline, lead 0 left the least echo "
        "1-2 s into a clip (`.scratch/echo-next/PLAN.md` E1). The default goes to 0; "
        "`--aec-lead-ms` still sets a lead.",
        "edits": [
            ("src/doc_chat/aec.py", E.T03_AEC),
            ("src/doc_chat/__main__.py", E.T03_MAIN),
        ],
        "tests": ["tests/doc_chat/test_aec_lead.py"],
        "regress": ["tests/doc_chat/test_aec.py"],
        "non_goals": "no change to `set_stream_delay_ms`, the frame logic or `RemotePlayer`; no "
        "other file.",
    },
    {
        "n": "04",
        "slug": "guard-warmup",
        "title": "a cautious echo bar for the first second of each reply (--barge-warmup-s)",
        "why": "With the canceller, the guard learns its coupling from cancelled audio, so its "
        "bar sits near the room floor, and the canceller leaks while it settles at the start of a "
        "reply: 7 of 20 replies cut themselves off live. Replayed from the trace, using the echo "
        "model's prior for the first 1.0 s (bar >= margin x prior x played) removed all 7 and kept "
        "every cued interruption (`.scratch/echo-next/PLAN.md` E2). Behind a flag, off by default; "
        "the model still learns against the ordinary bar.",
        "edits": [
            ("src/doc_chat/bargein.py", E.T04_BARGEIN),
            ("src/doc_chat/__main__.py", E.T04_MAIN),
        ],
        "tests": ["tests/doc_chat/test_guard_warmup.py"],
        "regress": ["tests/doc_chat/test_bargein.py", "tests/doc_chat/test_duck.py"],
        "non_goals": "no change to `EchoModel`, `OnsetDetector`, the Silero stage or the duck; "
        "nothing changes without `--barge-warmup-s`; no other file.",
        "last": True,
    },
]


def count_tests(path: Path) -> int:
    return sum(1 for line in path.read_text().splitlines() if line.startswith(("def test_",
                                                                             "async def test_")))


def main() -> None:
    out = HERE / "tickets"
    out.mkdir(exist_ok=True)
    state = {
        rel: (REPO / rel).read_text()
        for rel in ("src/doc_chat/__main__.py", "src/doc_chat/bargein.py", "src/doc_chat/aec.py")
    }
    for t in TICKETS:
        before = {rel: len(state[rel].splitlines()) for rel, _ in t["edits"]}
        for rel, pairs in t["edits"]:
            for old, new in pairs:
                assert state[rel].count(old) == 1, (t["n"], rel, old[:80])
                state[rel] = state[rel].replace(old, new)
        rows = []
        for rel, _ in t["edits"]:
            after = len(state[rel].splitlines())
            rows.append(f"| `{rel}` | edit (list below) | **{after}** (now {before[rel]}; "
                        f"exactly {after} after the edits) |")
        tests_n = 0
        for rel in t["tests"]:
            p = HERE / "skeleton" / rel
            n_lines = len(p.read_text().splitlines())
            tests_n += count_tests(p)
            rows.append(f"| `{rel}` | new, verbatim | **{n_lines}** |")
        files = [rel for rel, _ in t["edits"]] + t["tests"]
        L = [
            f"# Ticket {t['n']}: {t['title']}",
            "",
            "**Status:** ready",
            "**Date:** 2026-10-24",
            "**Blocked by:** " + ("None" if t["n"] == "01" else f"ticket {int(t['n']) - 1:02d} "
                                  "(edits the same `__main__.py`; run in order)"),
            "",
            t["why"],
            "",
            "Read `GUARDRAILS.md` (this folder) first; it binds this ticket.",
            "",
            "## Files",
            "",
            "| File | Action | Line cap |",
            "|---|---|---|",
            *rows,
            "",
            f"**Non-goals / touch nothing else:** {t['non_goals']}",
            "",
            "**Security-sensitive:** no (local text and audio logic; no secrets, no network).",
            "",
            "## Edits",
            "",
            "Apply in this order. Each OLD block occurs exactly once in the file as it is at that "
            "moment; replace it with NEW, byte-for-byte.",
            "",
        ]
        k = 0
        for rel, pairs in t["edits"]:
            for old, new in pairs:
                k += 1
                L += [f"### Edit {k}: `{rel}`", "", "OLD:", "", f"{FENCE}python", old, FENCE, "",
                      "NEW:", "", f"{FENCE}python", new, FENCE, ""]
        L += ["## New test file(s), verbatim", ""]
        for rel in t["tests"]:
            L += [f"### `{rel}`", "", f"{FENCE}python",
                  (HERE / "skeleton" / rel).read_text().rstrip("\n"), FENCE, ""]
        quoted = " ".join(files)
        proof = [
            (f"{UV} ruff check {quoted}", "All checks passed!"),
            (f"{UV} ruff format --check {quoted}", f"{len(files)} files already formatted"
             if len(files) > 1 else "1 file already formatted"),
            (f"{UV} mypy", "Success: no issues found in 54 source files"),
            (f"{UV} pytest -q -p no:cacheprovider {' '.join(t['tests'])}",
             f"{tests_n} passed"),
            (f"{UV} pytest -q -p no:cacheprovider {' '.join(t['regress'])}",
             "all passed, 0 failed (count printed)"),
        ]
        if t.get("last"):
            proof.append((f"{UV} pytest -q -p no:cacheprovider",
                          "exactly 1 failed: tests/doc_chat/test_flow.py::"
                          "test_llm_echo_is_accepted_without_documents (needs a speech server; "
                          "known, fails before these tickets too); everything else passes or "
                          "skips"))
        L += ["## Proof", "", "Run from the repository root, in order. Expected last line in the "
              "right column.", "", "| # | Command | Expected |", "|---|---|---|"]
        for i, (cmd, exp) in enumerate(proof, 1):
            L.append(f"| {i} | `{cmd}` | {exp} |")
        L += ["", "## Deviations", "", "(none yet)", "", "## Execution Log", "", ""]
        (out / f"{t['n']}-{t['slug']}.md").write_text("\n".join(L))
        print("wrote", t["n"], t["slug"], "tests", tests_n)


if __name__ == "__main__":
    main()
