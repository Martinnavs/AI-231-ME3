"""Y1: the device tools can run as a stdio child of the agent when the agent is on the Pi."""

import importlib.util
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pi_device_mcp", Path(__file__).resolve().parents[1] / "scripts" / "pi_device_mcp.py"
)
assert SPEC and SPEC.loader
pi_device_mcp = importlib.util.module_from_spec(SPEC)
sys.modules["pi_device_mcp"] = pi_device_mcp
SPEC.loader.exec_module(pi_device_mcp)


def test_stdio_serves_without_a_token_or_a_network(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ran: list[str] = []

    class Fake:
        def run(self, transport: str) -> None:
            ran.append(transport)

    monkeypatch.delenv("LAN_SPEECH_TOKEN", raising=False)
    monkeypatch.setattr(pi_device_mcp, "build_server", lambda **kw: Fake())
    assert pi_device_mcp.main(["--stdio"]) == 0 and ran == ["stdio"]


def test_the_network_mode_still_needs_the_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("LAN_SPEECH_TOKEN", raising=False)
    assert pi_device_mcp.main([]) == 2
