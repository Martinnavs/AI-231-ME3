"""Write the latency tickets (R3 fast path, R2 agent side, R2 doc_chat side) from real file states.

    python3 .scratch/latency/gen_tickets.py

States: repo -> after R3 (edits_r3.py applied, ruff-formatted) -> after R2 (= overlay/, which is
R2+R3 applied and formatted). Each ticket's OLD/NEW blocks are diff hunks between two states,
widened until OLD is unique at the moment it is applied, so the result is byte-exact.
"""

from __future__ import annotations

import difflib
import importlib.util
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OVERLAY = HERE / "overlay"
FENCE = "````"
ROOT_UV = "uv run --offline --group laptop"
PIPE_UV = "uv run --offline"


def load(name: str):  # noqa: ANN201
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def overlay_path(rel: str) -> Path:
    return OVERLAY / rel if rel.startswith("pipeline/") else OVERLAY / "root" / rel


def fmt(rel: str, text: str) -> str:
    if not rel.endswith(".py"):
        return text
    cwd = REPO / "pipeline" if rel.startswith("pipeline/") else REPO
    name = rel.removeprefix("pipeline/")
    out = subprocess.run(["uv", "run", "--offline", "ruff", "format", "--stdin-filename", name, "-"],
                         cwd=cwd, input=text, capture_output=True, text=True, check=True)  # fmt: skip
    return out.stdout


def after_r3(rel: str) -> str:
    r3 = load("edits_r3")
    text = (REPO / rel).read_text()
    for old, new in r3.EDITS.get(rel, []):
        assert text.count(old) == 1, (rel, old[:60])
        text = text.replace(old, new)
    if rel in r3.APPEND:
        text = text.rstrip("\n") + "\n" + r3.APPEND[rel].strip("\n") + "\n"
    return fmt(rel, text)


def hunks(before: str, after: str) -> list[tuple[str, str]]:
    """(old, new) blocks turning `before` into `after`, applied top to bottom."""
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
           if op[0] != "equal"]  # fmt: skip
    groups: list[list[tuple[str, int, int, int, int]]] = []
    for op in ops:  # merge changes closer than 3 lines
        if groups and op[1] - groups[-1][-1][2] < 3:
            groups[-1].append(op)
        else:
            groups.append([op])
    out = []
    state = before
    shift = 0  # line offset of `state` against `a` from earlier applied hunks
    for g in groups:
        i1, i2, j1, j2 = g[0][1], g[-1][2], g[0][3], g[-1][4]
        ctx = 1
        while True:
            lo, hi = max(0, i1 - ctx), min(len(a), i2 + ctx)
            old = "".join(a[lo:hi])
            new = "".join(a[lo:i1]) + "".join(b[j1:j2]) + "".join(a[i2:hi])
            if old and state.count(old) == 1:
                break
            ctx += 1
        state = state.replace(old, new)
        out.append((old.rstrip("\n"), new.rstrip("\n")))
        shift += (j2 - j1) - (i2 - i1)
    assert state == after, "hunks do not reproduce the target"
    return out


def lang(rel: str) -> str:
    return {".py": "python", ".txt": "text", ".json": "json"}[Path(rel).suffix]


TICKETS = [
    {
        "n": "01",
        "slug": "fast-path",
        "title": "fast path: well-known commands skip the LLM (R3)",
        "blocked": "None",
        "why": "Common, unambiguous commands (lights, music, volume up/down, timers, alarms, "
        "the speaker volume with a number) are matched by plain rules on the last user message "
        "and run the real tool directly, with a fixed spoken result, so they never wait for "
        "the LLM (two requests per tool turn, spikes of 0.7-7.7 s seen in the spoken check). "
        "Anything not matched goes to the LLM exactly as before. Checked by the assistant on a "
        "scratch copy: 11 of the 82 single-turn dry-run prompts match, all with the expected "
        "tool and arguments.",
        "edited": ["pipeline/src/voice_pipeline/agent_server.py", "pipeline/tests/test_agent_server.py"],
        "new": ["pipeline/src/voice_pipeline/fast_path.py", "pipeline/tests/test_fast_path.py"],
        "non_goals": "no change to the root project, `agent.py`, `agent_tools.py`, the prompts or "
        "either project's `pyproject.toml`/`uv.lock`; no fillers (tickets 02-03); never start the "
        "agent server or run the dry run (paid API).",
        "security": "no secrets; the fast path calls the same tool handlers the LLM would, with "
        "arguments from fixed rules.",
        "proof": [
            (f"cd pipeline && {PIPE_UV} ruff check src tests", "All checks passed!"),
            (f"cd pipeline && {PIPE_UV} ruff format --check src tests", "14 files already formatted"),
            (f"cd pipeline && {PIPE_UV} mypy", "Success: no issues found in 9 source files"),
            (f"cd pipeline && {PIPE_UV} pytest -q -p no:cacheprovider tests/test_fast_path.py "
             "tests/test_agent_server.py", "36 passed"),
            (f"cd pipeline && {PIPE_UV} pytest -q -p no:cacheprovider", "58 passed"),
        ],
    },
    {
        "n": "02",
        "slug": "tool-say",
        "title": "agent side of fillers: the tools' `say` sentence and the tool event (R2)",
        "blocked": "ticket 01 (both edit `agent_server.py` and `test_agent_server.py`; the OLD "
        "blocks below are written against ticket 01's result)",
        "why": "Every tool gets an optional `say` argument: a short sentence the model writes "
        "while it calls the tool (\"Checking the weather in Baguio.\"). It is spoken the moment "
        "the call starts and never reaches the tool. When the model leaves it out, the agent "
        "server sends `{\"event\": \"tool\", \"name\": ...}` so the client can play that tool's "
        "pre-recorded line (ticket 03). The system prompt gets one line asking for `say`.",
        "edited": ["pipeline/src/voice_pipeline/agent.py", "pipeline/src/voice_pipeline/agent_tools.py",
                   "pipeline/src/voice_pipeline/agent_server.py",
                   "pipeline/config/eval/system_agent.txt", "pipeline/tests/test_agent_server.py"],
        "new": ["pipeline/tests/test_say.py"],
        "non_goals": "no change to the root project (ticket 03), `fast_path.py`, either project's "
        "`pyproject.toml`/`uv.lock`; never start the agent server or run the dry run (paid API).",
        "security": "no secrets; `say` is model text, spoken only, never passed to a tool.",
        "proof": [
            (f"cd pipeline && {PIPE_UV} ruff check src tests", "All checks passed!"),
            (f"cd pipeline && {PIPE_UV} ruff format --check src tests", "15 files already formatted"),
            (f"cd pipeline && {PIPE_UV} mypy", "Success: no issues found in 9 source files"),
            (f"cd pipeline && {PIPE_UV} pytest -q -p no:cacheprovider tests/test_say.py "
             "tests/test_agent_server.py", "8 passed"),
            (f"cd pipeline && {PIPE_UV} pytest -q -p no:cacheprovider", "61 passed"),
        ],
    },
    {
        "n": "03",
        "slug": "doc-chat-fillers",
        "title": "doc_chat fillers: stall, tool line, notice and give-up (R2)",
        "blocked": "ticket 02 (this client reads its `{\"event\": \"tool\"}` message)",
        "why": "With `--fillers`, doc_chat plays short pre-recorded lines while a slow answer "
        "waits, timed from when the transcript is ready: a stall line at 1.2 s, the tool's "
        "line when a tool event arrives first, an honest notice at 2.5 s, and at 8 s it gives up "
        "with an apology and drops the turn. Nothing plays once the answer has started. The "
        "phrases were reviewed by the owner (`.scratch/latency/fillers-draft.md`); their audio "
        "is synthesised once at start. Fillers stay out of the chat history.",
        "edited": ["src/doc_chat/__main__.py", "src/doc_chat/agent_client.py"],
        "new": ["src/doc_chat/fillers.py", "config/fillers.json", "tests/doc_chat/test_fillers.py",
                "tests/doc_chat/test_fillers_session.py"],
        "non_goals": "no change to `pipeline/` (tickets 01-02), `pyproject.toml` or `uv.lock`; "
        "no new phrases; never start the speech server, the agent server or a live session.",
        "security": "no secrets.",
        "proof": [
            (f"{ROOT_UV} ruff check src/doc_chat tests/doc_chat", "All checks passed!"),
            (f"{ROOT_UV} ruff format --check src/doc_chat/__main__.py src/doc_chat/agent_client.py "
             "src/doc_chat/fillers.py tests/doc_chat/test_fillers.py "
             "tests/doc_chat/test_fillers_session.py", "5 files already formatted"),
            (f"{ROOT_UV} mypy", "Success: no issues found in 56 source files"),
            (f"{ROOT_UV} pytest -q -p no:cacheprovider tests/doc_chat/test_fillers.py "
             "tests/doc_chat/test_fillers_session.py", "10 passed"),
            (f"{ROOT_UV} pytest -q -p no:cacheprovider tests/doc_chat --deselect "
             "tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents", "253 passed, 3 skipped, 1 deselected"),
        ],
    },
]


def main() -> None:
    r3_files = set(load("edits_r3").EDITS) | set(load("edits_r3").APPEND)
    states: dict[str, str] = {}
    out = HERE / "tickets"
    out.mkdir(exist_ok=True)
    for t in TICKETS:
        rows, edits = [], []
        for rel in t["edited"]:
            before = states.get(rel, (REPO / rel).read_text())
            if t["n"] == "01":
                after = after_r3(rel)
                assert rel in r3_files
            else:
                after = overlay_path(rel).read_text()
            states[rel] = after
            n_before, n_after = len(before.splitlines()), len(after.splitlines())
            rows.append(f"| `{rel}` | edit (list below) | **{n_after}** (now {n_before}) |")
            edits += [(rel, old, new) for old, new in hunks(before, after)]
        for rel in t["new"]:
            n = len(overlay_path(rel).read_text().splitlines())
            rows.append(f"| `{rel}` | new, verbatim | **{n}** |")
        lines = [
            f"# Ticket {t['n']}: {t['title']}", "",
            "**Status:** ready", "**Date:** 2026-10-24", f"**Blocked by:** {t['blocked']}", "",
            t["why"], "",
            "Read `GUARDRAILS.md` (this folder) first; it binds this ticket.", "",
            "## Files", "", "| File | Action | Line cap |", "|---|---|---|", *rows, "",
            f"**Non-goals / touch nothing else:** {t['non_goals']}", "",
            f"**Security-sensitive:** {t['security']}", "",
            "## Edits", "",
            "Apply in this order. Each OLD block occurs exactly once in the file as it is at that "
            "moment; replace it with NEW, byte-for-byte.", "",
        ]  # fmt: skip
        for k, (rel, old, new) in enumerate(edits, 1):
            lines += [f"### Edit {k}: `{rel}`", "", "OLD:", "", f"{FENCE}{lang(rel)}", old, FENCE,
                      "", "NEW:", "", f"{FENCE}{lang(rel)}", new, FENCE, ""]  # fmt: skip
        lines += ["## New files, verbatim", "",
                  "Create each file with exactly this content (byte-for-byte, final newline "
                  "included).", ""]  # fmt: skip
        for rel in t["new"]:
            body = overlay_path(rel).read_text().rstrip("\n")
            lines += [f"### `{rel}`", "", f"{FENCE}{lang(rel)}", body, FENCE, ""]
        lines += ["## Proof", "", "Run from the repository root, in order. Expected last line in "
                  "the right column.", "", "| # | Command | Expected |", "|---|---|---|"]  # fmt: skip
        lines += [f"| {i} | `{c}` | {e or 'EXPECTED'} |" for i, (c, e) in enumerate(t["proof"], 1)]
        lines += ["", "## Deviations", "", "(none yet)", "", "## Execution Log", "", ""]
        (out / f"{t['n']}-{t['slug']}.md").write_text("\n".join(lines))
        print(f"wrote {t['n']}-{t['slug']}.md: {len(edits)} edits, {len(t['new'])} new files")
    for rel, text in states.items():
        assert text == overlay_path(rel).read_text(), f"final state differs from overlay: {rel}"
    print("final states match the overlay")


if __name__ == "__main__":
    main()
