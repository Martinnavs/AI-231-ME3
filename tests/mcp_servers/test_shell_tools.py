from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from voice_agent.mcp_servers import code
from voice_agent.sandbox.runner import preflight

_problems = preflight().problems
needs_sandbox = pytest.mark.skipif(
    bool(_problems), reason="sandbox not ready: " + "; ".join(_problems)
)


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(code._state, "root", root)
    monkeypatch.setattr(code._state, "media_root", root / "media", raising=False)
    monkeypatch.setattr(code._state, "lock", asyncio.Lock())
    yield root


async def test_bash_refuses_blocked_command(box: Path) -> None:
    with pytest.raises(ValueError, match="can't run sudo"):
        await code.bash("sudo ls")


@needs_sandbox
async def test_bash_runs_writes_inside_and_sees_the_host_read_only(box: Path) -> None:
    out = await code.bash("echo hi > a.txt && cat a.txt && cat /etc/os-release | head -1")
    assert out["returncode"] == 0 and str(out["stdout"]).startswith("hi\n")
    assert (box / "a.txt").read_text() == "hi\n"
    assert (await code.bash("touch /etc/forbidden"))["returncode"] != 0
    assert (await code.bash("ls /home"))["returncode"] != 0
    assert (await code.bash("exit 3"))["returncode"] == 3
    assert (await code.bash("sleep 5", timeout_s=1))["timed_out"] is True


@needs_sandbox
async def test_bash_has_no_network(box: Path) -> None:
    out = await code.bash(
        "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 80), 2)\""
    )
    assert out["returncode"] != 0


async def test_edit_file(box: Path) -> None:
    await code.write_file("a.txt", "one two two")
    assert await code.edit_file("a.txt", "one", "1") == {"path": "a.txt", "bytes": 9}
    assert (box / "a.txt").read_text() == "1 two two"
    with pytest.raises(ValueError, match="occurs 2 times"):
        await code.edit_file("a.txt", "two", "2")
    with pytest.raises(ValueError, match="occurs 0 times"):
        await code.edit_file("a.txt", "zzz", "2")
    with pytest.raises(ValueError, match="outside sandbox root"):
        await code.edit_file("../a.txt", "1", "2")
    with pytest.raises(ValueError, match="not found"):
        await code.edit_file("missing.txt", "1", "2")


@needs_sandbox
async def test_grep(box: Path) -> None:
    await code.write_file("src/a.py", "alpha\nbeta\n")
    await code.write_file("src/b.txt", "beta\n")
    assert await code.grep("alp", "src") == "src/a.py:1:alpha"
    assert (await code.grep("beta", ".", "*.py")).splitlines() == ["src/a.py:2:beta"]
    assert await code.grep("zzz") == "no matches"
    await code.write_file("many.txt", "".join(f"hit {i}\n" for i in range(80)))
    assert len((await code.grep("hit", "many.txt")).splitlines()) == 50
    await code.write_file("wide.txt", "x" * 1000)
    assert len(await code.grep("x+", "wide.txt")) <= 300
    with pytest.raises(ValueError, match="outside sandbox root"):
        await code.grep("a", "..")
