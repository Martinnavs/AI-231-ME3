# Ticket 02: Pi device MCP server over the network (volume, device status)

**Status:** delivered-for-review
**Date:** 2026-10-24
**Blocked by:** None (tickets 01 and 02 are independent: disjoint files, no data flows between them)

An MCP server that runs on the Raspberry Pi and is reached by the agent over streamable HTTP with the session's bearer token: `get_volume`, `set_volume(percent)` (clamped 0-100, `wpctl` with an argument list, no shell), `device_status` (CPU temperature, uptime, load, free memory, speaker connected). It uses only the `mcp` SDK, which the Pi already has (1.30.0, same as the laptop). The assistant ran it over HTTP on the laptop: tools listed and called with the token, refused with a wrong one.

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `scripts/pi_device_mcp.py` | new, verbatim | **139** |
| `tests/test_pi_device_mcp.py` | new, verbatim | **103** |

**Non-goals / touch nothing else:** no change to any existing file; nothing is deployed to the Pi (the owner decides that); no timers (the smart_home tool has TIMER); never start the server.

**Security-sensitive:** YES: a network server that changes device state. The token is required (the server refuses to start without LAN_SPEECH_TOKEN, compares with hmac), volume is clamped, commands are argument lists. Copy the code exactly; do not relax any of it.

## New files, verbatim

Create each file with exactly this content (byte-for-byte, final newline included).

### `scripts/pi_device_mcp.py`

````python
"""The Pi's device tools as an MCP server on the network: speaker volume and device status.

Runs on the Raspberry Pi (no model: only the `mcp` SDK, already installed). The agent on the
laptop connects over streamable HTTP with the session's bearer token, as `pi_speaker.py` does.

    LAN_SPEECH_TOKEN=... .venv/bin/python -u scripts/pi_device_mcp.py --port 8790

Tools: get_volume, set_volume (0-100, through `wpctl` on the speaker's sink), device_status (CPU
temperature, uptime, load, free memory, whether the speaker is connected).
"""

from __future__ import annotations

import argparse
import hmac
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

SINK = "CD002"  # a substring of the speaker sink's name in `wpctl status`
SINK_LINE = re.compile(r"(\d+)\.\s+([^\[\n]+?)\s+\[vol:\s*([\d.]+)")

Run = Callable[[list[str]], str]
Read = Callable[[str], str]


def run_command(cmd: list[str]) -> str:
    """stdout of `cmd` (no shell; empty if it fails or takes more than 3 s)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read_file(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def find_sink(status: str, name: str = SINK) -> tuple[str, float] | None:
    """(id, volume 0-1) of the first sink whose name contains `name`, from `wpctl status`."""
    sinks = status.split("Sinks:", 1)[1].split("Sources:", 1)[0] if "Sinks:" in status else ""
    for sink_id, label, volume in SINK_LINE.findall(sinks):
        if name in label:
            return sink_id, float(volume)
    return None


def build_server(run: Run = run_command, read: Read = read_file, sink: str = SINK) -> FastMCP:
    """A FastMCP server with the three tools; `run` and `read` are replaced in tests."""
    server = FastMCP("pi_device")

    @server.tool(description="The speaker's volume on the Raspberry Pi, in percent.")
    def get_volume() -> str:
        found = find_sink(run(["wpctl", "status"]), sink)
        if found is None:
            return "The speaker is not connected to the Pi."
        return f"The speaker volume is {round(found[1] * 100)} percent."

    @server.tool(description="Set the speaker's volume on the Raspberry Pi, in percent (0 to 100).")
    def set_volume(percent: int) -> str:
        found = find_sink(run(["wpctl", "status"]), sink)
        if found is None:
            return "The speaker is not connected to the Pi."
        level = max(0, min(100, int(percent)))
        run(["wpctl", "set-volume", found[0], f"{level / 100:.2f}"])
        return f"The speaker volume is now {level} percent."

    @server.tool(
        description=(
            "The Raspberry Pi's status: CPU temperature, uptime, load, free memory and whether "
            "the speaker is connected."
        )
    )
    def device_status() -> dict[str, Any]:
        temp = read("/sys/class/thermal/thermal_zone0/temp").strip()
        uptime = read("/proc/uptime").split()
        load = read("/proc/loadavg").split()
        mem = dict(re.findall(r"(\w+):\s+(\d+) kB", read("/proc/meminfo")))
        return {
            "cpu_temperature_c": round(int(temp) / 1000, 1) if temp.isdigit() else None,
            "uptime_minutes": round(float(uptime[0]) / 60) if uptime else None,
            "load_1min": float(load[0]) if load else None,
            "free_memory_mb": round(int(mem["MemAvailable"]) / 1024)
            if "MemAvailable" in mem
            else None,
            "speaker_connected": find_sink(run(["wpctl", "status"]), sink) is not None,
        }

    return server


class BearerAuth:
    """ASGI middleware: 401 unless the request carries `Authorization: Bearer <token>`."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            given = headers.get(b"authorization", b"").removeprefix(b"Bearer ")
            if not hmac.compare_digest(given, self.token):
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized\n"})
                return
        await self.app(scope, receive, send)


def main(argv: Sequence[str] | None = None) -> int:
    import uvicorn

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="0.0.0.0")  # the cable and Wi-Fi; the token guards it
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--sink", default=SINK, help="substring of the speaker sink's name")
    args = ap.parse_args(argv)
    token = os.environ.get("LAN_SPEECH_TOKEN")
    if not token:
        print("LAN_SPEECH_TOKEN is not set; refusing to serve device tools without a token")
        return 2
    server = build_server(sink=args.sink)
    server.settings.stateless_http = True
    app = BearerAuth(server.streamable_http_app(), token)
    print(f"device tools on {args.host}:{args.port}/mcp", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
````

### `tests/test_pi_device_mcp.py`

````python
"""scripts/pi_device_mcp.py: the Pi's device tools, with the commands and files faked."""

import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pi_device_mcp import BearerAuth, build_server, find_sink  # noqa: E402

STATUS = """Audio
 ├─ Devices:
 ├─ Sinks:
 │      35. Built-in Audio Stereo               [vol: 0.40]
 │  *   58. CD002-AUDIO Analog Stereo           [vol: 0.80]
 │      69. Built-in Audio Digital Stereo (HDMI) [vol: 0.40]
 │
 ├─ Sources:
 │      40. CD002-AUDIO Mono                    [vol: 1.00]
"""
FILES = {
    "/sys/class/thermal/thermal_zone0/temp": "53556\n",
    "/proc/uptime": "5941.74 23372.08\n",
    "/proc/loadavg": "0.10 0.09 0.04 2/326 1887\n",
    "/proc/meminfo": "MemTotal:        3887804 kB\nMemAvailable:    3332332 kB\n",
}


def _server(status: str = STATUS) -> tuple[Any, list[list[str]]]:
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        return status if cmd == ["wpctl", "status"] else ""

    return build_server(run=run, read=lambda p: FILES.get(p, "")), calls


async def _call(server: Any, tool: str, args: dict[str, object] | None = None) -> Any:
    _content, structured = await server.call_tool(tool, args or {})
    return structured["result"]


def test_find_sink_reads_the_speaker_from_wpctl_status() -> None:
    assert find_sink(STATUS) == ("58", 0.8)
    assert find_sink(STATUS.replace("CD002-AUDIO Analog", "Other")) is None  # sources do not count


async def test_lists_the_three_tools() -> None:
    server, _ = _server()
    names = sorted(t.name for t in await server.list_tools())
    assert names == ["device_status", "get_volume", "set_volume"]


async def test_get_volume() -> None:
    server, _ = _server()
    assert await _call(server, "get_volume") == "The speaker volume is 80 percent."


async def test_set_volume_is_clamped_and_uses_wpctl_without_a_shell() -> None:
    server, calls = _server()
    assert await _call(server, "set_volume", {"percent": 150}) == (
        "The speaker volume is now 100 percent."
    )
    assert calls[-1] == ["wpctl", "set-volume", "58", "1.00"]
    await _call(server, "set_volume", {"percent": 35})
    assert calls[-1] == ["wpctl", "set-volume", "58", "0.35"]


async def test_no_speaker_is_a_sentence() -> None:
    server, calls = _server(status="Audio\n ├─ Sinks:\n ├─ Sources:\n")
    assert await _call(server, "set_volume", {"percent": 50}) == (
        "The speaker is not connected to the Pi."
    )
    assert all(c[1] != "set-volume" for c in calls)


async def test_device_status_reads_proc_and_sys() -> None:
    server, _ = _server()
    _content, status = await server.call_tool("device_status", {})
    assert status == {
        "cpu_temperature_c": 53.6,
        "uptime_minutes": 99,
        "load_1min": 0.1,
        "free_memory_mb": 3254,
        "speaker_connected": True,
    }


async def test_requests_without_the_token_are_refused() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    transport = httpx.ASGITransport(app=BearerAuth(app, "secret"))
    async with httpx.AsyncClient(transport=transport, base_url="http://pi") as client:
        assert (await client.get("/mcp")).status_code == 401
        bad = await client.get("/mcp", headers={"Authorization": "Bearer wrong"})
        assert bad.status_code == 401
        good = await client.get("/mcp", headers={"Authorization": "Bearer secret"})
        assert good.status_code == 200
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `uv run --offline --group laptop ruff check scripts/pi_device_mcp.py tests/test_pi_device_mcp.py` | All checks passed! |
| 2 | `uv run --offline --group laptop ruff format --check scripts/pi_device_mcp.py tests/test_pi_device_mcp.py` | 2 files already formatted |
| 3 | `uv run --offline --group laptop mypy` | Success: no issues found in 55 source files |
| 4 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/test_pi_device_mcp.py` | 7 passed |

## Deviations

none

## Execution Log

### 2026-10-24 agy: DONE
- Proof: 1 ✅ (`uv run --offline --group laptop ruff check scripts/pi_device_mcp.py tests/test_pi_device_mcp.py`: All checks passed!) 2 ✅ (`uv run --offline --group laptop ruff format --check scripts/pi_device_mcp.py tests/test_pi_device_mcp.py`: 2 files already formatted) 3 ✅ (`uv run --offline --group laptop mypy`: Success: no issues found in 55 source files) 4 ✅ (`uv run --offline --group laptop pytest -q -p no:cacheprovider tests/test_pi_device_mcp.py`: 7 passed in 0.29s)
- Files:
  - `scripts/pi_device_mcp.py` (139 / 139)
  - `tests/test_pi_device_mcp.py` (103 / 103)
- Deviations: none
- Notes for reviewer: none


### 2026-10-24 reviewer (assistant): ACCEPTED
- `diff -rq orig work`: only this wave's four new files and the tickets changed.
- `cmp` against the tested skeleton: byte-identical. Deviations: none.
- Integrated into the repository; gate: ruff and mypy clean, pytest 634 passed, 9 skipped, 2 xfailed, 1 failed (the known `test_flow` case that needs a speech server).
