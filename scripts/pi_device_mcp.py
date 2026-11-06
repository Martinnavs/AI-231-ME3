"""The Pi's device tools as an MCP server on the network: speaker volume and device status.

Runs on the Raspberry Pi (no model: only the `mcp` SDK, already installed). The agent on the
laptop connects over streamable HTTP with the session's bearer token, as `pi_speaker.py` does.

    LAN_SPEECH_TOKEN=... .venv/bin/python -u scripts/pi_device_mcp.py --port 8790

With --stdio the agent server on the Pi starts it as a child (HARNESS=pi).

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
from mcp.server.transport_security import TransportSecuritySettings

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


def allowed_hosts(addresses: str) -> list[str]:
    """Host headers to accept, from `hostname -I`: the Pi's own addresses, any port.

    The SDK's DNS-rebinding check otherwise accepts only localhost, so the agent reaching the Pi
    by its address got 421 "Invalid Host header". The check stays on; it just knows the Pi."""
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    return hosts + [f"[{a}]:*" if ":" in a else f"{a}:*" for a in addresses.split()]


def build_server(
    run: Run = run_command, read: Read = read_file, sink: str = SINK, hosts: list[str] | None = None
) -> FastMCP:
    """A FastMCP server with the three tools; `run` and `read` are replaced in tests."""
    security = (
        TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts)
        if hosts
        else None
    )
    server = FastMCP("pi_device", transport_security=security)

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
    ap.add_argument(
        "--stdio",
        action="store_true",
        help="serve over stdin/stdout to the parent process (the agent running on this Pi); no "
        "network, no token",
    )
    args = ap.parse_args(argv)
    if args.stdio:
        build_server(sink=args.sink).run(transport="stdio")
        return 0
    token = os.environ.get("LAN_SPEECH_TOKEN")
    if not token:
        print("LAN_SPEECH_TOKEN is not set; refusing to serve device tools without a token")
        return 2
    server = build_server(sink=args.sink, hosts=allowed_hosts(run_command(["hostname", "-I"])))
    server.settings.stateless_http = True
    app = BearerAuth(server.streamable_http_app(), token)
    print(f"device tools on {args.host}:{args.port}/mcp", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
