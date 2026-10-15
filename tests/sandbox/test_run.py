from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from voice_agent.sandbox.runner import SandboxPolicy, SandboxResult, preflight, run_in_sandbox

_problems = preflight().problems
pytestmark = [pytest.mark.pi]
if _problems:
    pytestmark.append(pytest.mark.skip(reason="sandbox not ready: " + "; ".join(_problems)))


async def _run(tmp_path: Path, code: str, **kw: object) -> SandboxResult:
    (tmp_path / "s.py").write_text(textwrap.dedent(code))
    policy = SandboxPolicy(root=tmp_path, **kw)  # type: ignore[arg-type]
    return await run_in_sandbox(policy, ["python3", "s.py"])


async def test_no_network(tmp_path: Path) -> None:
    res = await _run(
        tmp_path,
        """
        import socket
        s = socket.socket(); s.settimeout(3)
        try:
            s.connect(("1.1.1.1", 53))
        except OSError:
            print("blocked")
        else:
            print("connected")
        """,
    )
    assert res.stdout.strip() == "blocked"


async def test_cannot_read_home(tmp_path: Path) -> None:
    home = Path.home()
    res = await _run(
        tmp_path,
        f"""
        import os
        print(os.path.exists({str(home / ".bashrc")!r}), os.path.exists({str(home)!r}))
        """,
    )
    assert res.stdout.split() == ["False", "False"]


async def test_cannot_write_outside(tmp_path: Path) -> None:
    res = await _run(
        tmp_path,
        """
        for p in ("/usr/x", "/etc/x", "/x"):
            try:
                open(p, "w").write("1")
            except OSError:
                print("denied")
        open("/sandbox/ok.txt", "w").write("1")
        """,
    )
    assert res.stdout.split() == ["denied"] * 3
    assert (tmp_path / "ok.txt").exists()


async def test_timeout_kills(tmp_path: Path) -> None:
    res = await _run(tmp_path, "while True: pass", timeout_s=2)
    assert res.timed_out and res.returncode == 124


async def test_memory_limit_kills(tmp_path: Path) -> None:
    res = await _run(tmp_path, "x = bytearray(1024**3); print('survived')", memory_max="128M")
    assert "survived" not in res.stdout
    assert res.returncode != 0
