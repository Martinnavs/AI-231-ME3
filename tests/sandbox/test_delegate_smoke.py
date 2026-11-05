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
