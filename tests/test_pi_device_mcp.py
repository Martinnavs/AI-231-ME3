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
