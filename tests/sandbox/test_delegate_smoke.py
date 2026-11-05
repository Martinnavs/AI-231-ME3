"""Real bwrap runs with the delegate policy (network on, host /etc visible, pygame dir bound)."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from voice_agent.mcp_servers import code
from voice_agent.sandbox.runner import SandboxPolicy, SandboxResult, preflight, run_in_sandbox

_problems = preflight().problems
pytestmark: list[pytest.MarkDecorator] = []
if _problems:
    pytestmark.append(pytest.mark.skip(reason="sandbox not ready: " + "; ".join(_problems)))


def _online() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    except OSError:
        return False
    return True


needs_net = pytest.mark.skipif(not _online(), reason="no internet from this machine")
PYLIBS = Path.home() / "agent_sandbox" / "pylibs"


async def _bash(root: Path, command: str, **kw: object) -> SandboxResult:
    policy = SandboxPolicy(root=root, host_readonly=True, network=True, **kw)  # type: ignore[arg-type]
    return await run_in_sandbox(policy, ["bash", "-c", command])


@needs_net
async def test_ping_works_with_the_network_on(tmp_path: Path) -> None:
    res = await _bash(tmp_path, "ping -c1 -W3 8.8.8.8")
    assert res.returncode == 0 and "1 received" in res.stdout


@needs_net
async def test_curl_works_with_the_network_on(tmp_path: Path) -> None:
    res = await _bash(tmp_path, "curl -sI -m8 https://example.com | head -1")
    assert res.stdout.startswith("HTTP/")


async def test_ping_and_curl_fail_with_the_network_off(tmp_path: Path) -> None:
    policy = SandboxPolicy(root=tmp_path, host_readonly=True)
    res = await run_in_sandbox(policy, ["bash", "-c", "curl -sI -m4 https://example.com"])
    assert res.returncode != 0


async def test_a_file_written_by_one_job_is_read_by_the_next(tmp_path: Path) -> None:
    await _bash(tmp_path, "echo 2 3 5 > primes.txt")
    res = await _bash(tmp_path, "cat primes.txt")
    assert res.stdout.strip() == "2 3 5"


@pytest.mark.skipif(not (PYLIBS / "pygame").is_dir(), reason="pygame not installed in pylibs")
async def test_pygame_imports_and_draws_headless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(code._state, "root", tmp_path)
    monkeypatch.setattr(code._state, "pylibs", PYLIBS)
    monkeypatch.setattr(code._state, "network", True)
    monkeypatch.setattr(code._state, "media_root", None)
    (tmp_path / "g.py").write_text(
        "import pygame\npygame.init()\ns = pygame.display.set_mode((32, 24))\n"
        "s.fill((1, 2, 3))\npygame.display.flip()\nprint(tuple(s.get_at((0, 0)))[:3])\n"
    )
    res = await code.run_python("g.py")
    assert res["returncode"] == 0 and "(1, 2, 3)" in str(res["stdout"]), res


_PROBE = """
import socket, sys
s = socket.create_connection(("127.0.0.1", {port}), timeout=5)
s.sendall(b"GET / HTTP/1.1\\r\\nHost: x\\r\\nUpgrade: websocket\\r\\nConnection: Upgrade\\r\\n"
          b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\\r\\nSec-WebSocket-Version: 13\\r\\n\\r\\n")
print(s.recv(64).split(b"\\r\\n")[0].decode())
"""


async def test_agent_server_rejects_a_sandboxed_job_that_has_no_token(tmp_path: Path) -> None:
    """The sandbox shares loopback with the laptop; the session token is what keeps it out."""
    import threading

    from websockets.sync.server import serve

    def guard(connection: object, request: object) -> object:
        header = request.headers.get("Authorization")  # type: ignore[attr-defined]
        if header == "Bearer smoke-token":
            return None
        return connection.respond(401, "Unauthorized\n")  # type: ignore[attr-defined]

    with serve(lambda ws: None, "127.0.0.1", 0, process_request=guard) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        res = await _bash(tmp_path, f"python3 -c '{_PROBE.format(port=port)}'; env")
        server.shutdown()
    assert "401" in res.stdout.splitlines()[0], res.stdout + res.stderr
    assert "smoke-token" not in res.stdout and "LAN_SPEECH_TOKEN" not in res.stdout
