"""Write ticket 03 (agent wiring) from edits_03.py and skeleton/pipeline/ (verbatim, exact caps).

    python3 .scratch/mcp-set/gen_ticket03.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location("edits", HERE / "edits_03.py")
assert spec and spec.loader
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)

FENCE = "````"
UV = "uv run --offline"
REPLACED = ["pipeline/src/voice_pipeline/agent_tools.py", "pipeline/config/eval/system_agent.txt"]
NEW = ["pipeline/tests/test_agent_tools.py"]


def lang(path: str) -> str:
    return {".py": "python", ".txt": "text", ".jsonl": "json"}[Path(path).suffix]


def main() -> None:
    state = {rel: (REPO / rel).read_text() for rel in E.EDITS}
    for rel, pairs in E.EDITS.items():
        for old, new in pairs:
            assert state[rel].count(old) == 1, (rel, old[:60])
            state[rel] = state[rel].replace(old, new)
    for rel, text in E.APPEND.items():
        state[rel] = state[rel].rstrip("\n") + "\n" + text + "\n"
    rows = []
    for rel in E.EDITS:
        before = len((REPO / rel).read_text().splitlines())
        after = len(state[rel].splitlines())
        how = "edit + append (below)" if rel in E.APPEND else "edit (list below)"
        rows.append(f"| `{rel}` | {how} | **{after}** (now {before}; exactly {after} after) |")
    for rel in REPLACED:
        n = len((HERE / "skeleton" / rel).read_text().splitlines())
        rows.append(f"| `{rel}` | replace whole file, verbatim | **{n}** |")
    for rel in NEW:
        n = len((HERE / "skeleton" / rel).read_text().splitlines())
        rows.append(f"| `{rel}` | new, verbatim | **{n}** |")
    lines = [
        "# Ticket 03: agent wiring for the real MCP tools",
        "",
        "**Status:** ready",
        "**Date:** 2026-10-24",
        "**Blocked by:** tickets 01 and 02 (landed: `src/voice_agent/mcp_servers/web_info.py`, "
        "`scripts/pi_device_mcp.py`); `mcp-server-time` is already in `pipeline/pyproject.toml` "
        "and `pipeline/uv.lock` (added by the assistant with the owner's yes; do not touch them)",
        "",
        "The agent gets its real tools: the official time server (stdio), the web_info server "
        "(stdio, root project) and the Pi's device server (streamable HTTP with "
        "`LAN_SPEECH_TOKEN`; optional, so the agent still starts when the Pi is off). TIME and "
        "WEATHER leave `smart_home` (the owner, 2026-10-24: the requirement is the ME2 phrases, "
        "not ME2's structure); the routing prompt sends time, weather and general knowledge to "
        "the real tools, speaker volume with a number and Pi status to the Pi, relative music "
        "volume stays `smart_home`. The dry run skips prompts whose tool is not available. "
        "Checked by the assistant on a scratch copy: the real servers start and list their "
        "tools; the dry run results are in the plan.",
        "",
        "Read `GUARDRAILS.md` (this folder) first; it binds this ticket. All commands in this "
        "ticket run from `pipeline/` (cd there first).",
        "",
        "## Files",
        "",
        "| File | Action | Line cap |",
        "|---|---|---|",
        *rows,
        "",
        "**Non-goals / touch nothing else:** no change to the root project, `pyproject.toml` or "
        "`uv.lock` of either project, `agent.py`, the doc_chat client; nothing is deployed to "
        "the Pi; never start the agent server or run the dry run (it calls the paid Gemini API).",
        "",
        "**Security-sensitive:** the Pi client sends the bearer token from `LAN_SPEECH_TOKEN`; "
        "copy exactly, never print or log the token.",
        "",
        "## Edits",
        "",
        "Apply in this order. Each OLD block occurs exactly once in the file as it is at that "
        "moment; replace it with NEW, byte-for-byte.",
        "",
    ]
    k = 0
    for rel, pairs in E.EDITS.items():
        for old, new in pairs:
            k += 1
            lines += [f"### Edit {k}: `{rel}`", "", "OLD:", "", f"{FENCE}{lang(rel)}", old, FENCE,
                      "", "NEW:", "", f"{FENCE}{lang(rel)}", new, FENCE, ""]  # fmt: skip
    for rel, text in E.APPEND.items():
        lines += [f"### Append to the end of `{rel}`", "",
                  "After the edits, add these lines at the end of the file (one JSON object per "
                  "line, the file ending with a newline):", "", f"{FENCE}json", text, FENCE, ""]  # fmt: skip
    lines += ["## Whole files, verbatim", "",
              "Replace each existing file entirely with this content, and create the new one "
              "(byte-for-byte, final newline included).", ""]  # fmt: skip
    for rel in REPLACED + NEW:
        body = (HERE / "skeleton" / rel).read_text().rstrip("\n")
        lines += [f"### `{rel}`", "", f"{FENCE}{lang(rel)}", body, FENCE, ""]
    proof = [
        (f"cd pipeline && {UV} ruff check src tests", "All checks passed!"),
        (f"cd pipeline && {UV} ruff format --check src tests", "11 files already formatted"),
        (f"cd pipeline && {UV} mypy", "Success: no issues found in 7 source files"),
        (f"cd pipeline && {UV} pytest -q -p no:cacheprovider tests/test_agent_tools.py",
         "8 passed"),
        (f"cd pipeline && {UV} pytest -q -p no:cacheprovider", "24 passed"),
    ]  # fmt: skip
    lines += ["## Proof", "", "Run from the repository root, in order. Expected last line in the "
              "right column.", "", "| # | Command | Expected |", "|---|---|---|"]  # fmt: skip
    lines += [f"| {i} | `{c}` | {e} |" for i, (c, e) in enumerate(proof, 1)]
    lines += ["", "## Deviations", "", "(none yet)", "", "## Execution Log", "", ""]
    (HERE / "tickets" / "03-agent-wiring.md").write_text("\n".join(lines))
    print("wrote 03-agent-wiring.md")


if __name__ == "__main__":
    main()
