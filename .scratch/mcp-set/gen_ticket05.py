"""Write ticket 05 (web_info speed) from skeleton05/ (whole-file replacement + new test).

    python3 .scratch/mcp-set/gen_ticket05.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FENCE = "````"
UV = "uv run --offline --group laptop"
REPLACED = "src/voice_agent/mcp_servers/web_info.py"
NEW = "tests/mcp_servers/test_web_info_cache.py"


def main() -> None:
    k = HERE / "skeleton05"
    rows = [
        f"| `{REPLACED}` | replace whole file, verbatim | **{len((k / REPLACED).read_text().splitlines())}** "
        f"(now {len((REPO / REPLACED).read_text().splitlines())}) |",
        f"| `{NEW}` | new, verbatim | **{len((k / NEW).read_text().splitlines())}** |",
    ]
    files = f"{REPLACED} {NEW}"
    proof = [
        (f"{UV} ruff check {files}", "All checks passed!"),
        (f"{UV} ruff format --check {files}", "2 files already formatted"),
        (f"{UV} mypy", "Success: no issues found in 55 source files"),
        (f"{UV} pytest -q -p no:cacheprovider tests/mcp_servers/test_web_info.py {NEW}",
         "9 passed"),
    ]  # fmt: skip
    lines = [
        "# Ticket 05: web_info speed (one kept connection, remembered places, cached forecasts)",
        "",
        "**Status:** ready",
        "**Date:** 2026-10-24",
        "**Blocked by:** None (ticket 01 landed; independent of ticket 03: disjoint files, and "
        "the tool names and arguments do not change)",
        "",
        "In the dry run the weather tool took about 3 s: Open-Meteo is about 270 ms away from "
        "here and every request opened a fresh TLS connection (about 0.6 s), twice per question. "
        "The server now keeps one HTTP client for its life, remembers place lookups, and reuses "
        "a forecast for 10 minutes. Measured by the assistant on the live APIs: a repeat question "
        "5 ms, a new place on a warm connection 0.55 s, Wikipedia 0.45-0.76 s; the first weather "
        "question of a session is still cold (about 3 s). Answers and error sentences unchanged.",
        "",
        "Read `GUARDRAILS.md` (this folder) first; it binds this ticket.",
        "",
        "## Files",
        "",
        "| File | Action | Line cap |",
        "|---|---|---|",
        *rows,
        "",
        "**Non-goals / touch nothing else:** no change to `tests/mcp_servers/test_web_info.py` "
        "(it must still pass unchanged), the tool names, arguments or sentences; no warm-up at "
        "start; no network in the tests.",
        "",
        "**Security-sensitive:** no.",
        "",
        "## Whole files, verbatim",
        "",
        "Replace the existing file entirely, and create the new one (byte-for-byte, final "
        "newline included).",
        "",
    ]
    for rel in (REPLACED, NEW):
        lines += [f"### `{rel}`", "", f"{FENCE}python", (k / rel).read_text().rstrip("\n"), FENCE, ""]
    lines += ["## Proof", "", "Run from the repository root, in order. Expected last line in the "
              "right column.", "", "| # | Command | Expected |", "|---|---|---|"]  # fmt: skip
    lines += [f"| {i} | `{c}` | {e} |" for i, (c, e) in enumerate(proof, 1)]
    lines += ["", "## Deviations", "", "(none yet)", "", "## Execution Log", "", ""]
    (HERE / "tickets" / "05-web-info-speed.md").write_text("\n".join(lines))
    print("wrote 05-web-info-speed.md")


if __name__ == "__main__":
    main()
