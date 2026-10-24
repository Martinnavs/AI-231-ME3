"""Write tickets 01-02 of the MCP set from skeleton/ (new files only, verbatim, exact caps).

    python3 .scratch/mcp-set/gen_tickets.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
UV = "uv run --offline --group laptop"
FENCE = "````"

TICKETS = [
    {
        "n": "01",
        "slug": "web-info-server",
        "title": "weather + Wikipedia MCP server (keyless public APIs)",
        "why": "A real MCP server next to the sample `search_docs`: `weather(place, day)` through "
        "Open-Meteo (geocoding + forecast) and `wiki_summary(topic)` through Wikipedia's REST API, "
        "both without API keys, answering in one or two sentences the assistant can say. Network "
        "problems come back as a sentence. Same fastmcp 2.14.7 setup as "
        "`src/voice_agent/mcp_servers/docs.py`. Checked against the live APIs by the assistant "
        "(Wikimedia refuses user agents without a contact, hence the repository URL in it).",
        "files": [
            "src/voice_agent/mcp_servers/web_info.py",
            "tests/mcp_servers/test_web_info.py",
        ],
        "tests": "tests/mcp_servers/test_web_info.py",
        "n_tests": 7,
        "non_goals": "no change to any existing file (the agent wiring is ticket 03); no new "
        "dependency (httpx and fastmcp are installed); no network access from the tests.",
        "security": "no secrets; sends the place name or topic the user asked for to Open-Meteo "
        "and Wikipedia.",
    },
    {
        "n": "02",
        "slug": "pi-device-server",
        "title": "Pi device MCP server over the network (volume, device status)",
        "why": "An MCP server that runs on the Raspberry Pi and is reached by the agent over "
        "streamable HTTP with the session's bearer token: `get_volume`, `set_volume(percent)` "
        "(clamped 0-100, `wpctl` with an argument list, no shell), `device_status` (CPU "
        "temperature, uptime, load, free memory, speaker connected). It uses only the `mcp` SDK, "
        "which the Pi already has (1.30.0, same as the laptop). The assistant ran it over HTTP on "
        "the laptop: tools listed and called with the token, refused with a wrong one.",
        "files": ["scripts/pi_device_mcp.py", "tests/test_pi_device_mcp.py"],
        "tests": "tests/test_pi_device_mcp.py",
        "n_tests": 7,
        "non_goals": "no change to any existing file; nothing is deployed to the Pi (the owner "
        "decides that); no timers (the smart_home tool has TIMER); never start the server.",
        "security": "YES: a network server that changes device state. The token is required "
        "(the server refuses to start without LAN_SPEECH_TOKEN, compares with hmac), volume is "
        "clamped, commands are argument lists. Copy the code exactly; do not relax any of it.",
    },
]


def main() -> None:
    out = HERE / "tickets"
    out.mkdir(exist_ok=True)
    for t in TICKETS:
        files: list[str] = t["files"]  # type: ignore[assignment]
        rows = [
            f"| `{f}` | new, verbatim | **{len((HERE / 'skeleton' / f).read_text().splitlines())}** |"
            for f in files
        ]
        joined = " ".join(files)
        proof = [
            (f"{UV} ruff check {joined}", "All checks passed!"),
            (f"{UV} ruff format --check {joined}", "2 files already formatted"),
            (f"{UV} mypy", "Success: no issues found in 55 source files"),
            (f"{UV} pytest -q -p no:cacheprovider {t['tests']}", f"{t['n_tests']} passed"),
        ]
        lines = [
            f"# Ticket {t['n']}: {t['title']}",
            "",
            "**Status:** ready",
            "**Date:** 2026-10-24",
            "**Blocked by:** None (tickets 01 and 02 are independent: disjoint files, no data "
            "flows between them)",
            "",
            str(t["why"]),
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
            f"**Security-sensitive:** {t['security']}",
            "",
            "## New files, verbatim",
            "",
            "Create each file with exactly this content (byte-for-byte, final newline included).",
            "",
        ]
        for f in files:
            body = (HERE / "skeleton" / f).read_text().rstrip("\n")
            lines += [f"### `{f}`", "", f"{FENCE}python", body, FENCE, ""]
        lines += [
            "## Proof",
            "",
            "Run from the repository root, in order. Expected last line in the right column.",
            "",
            "| # | Command | Expected |",
            "|---|---|---|",
        ]
        lines += [f"| {i} | `{c}` | {e} |" for i, (c, e) in enumerate(proof, 1)]
        lines += ["", "## Deviations", "", "(none yet)", "", "## Execution Log", "", ""]
        (out / f"{t['n']}-{t['slug']}.md").write_text("\n".join(lines))
        print("wrote", t["n"], t["slug"])


if __name__ == "__main__":
    main()
