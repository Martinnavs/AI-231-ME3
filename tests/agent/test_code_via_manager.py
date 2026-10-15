from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from voice_agent.agent.mcp_clients import McpClientManager
from voice_agent.config import McpServerSpec
from voice_agent.sandbox import runner

_SECRET = "sk-test-not-a-real-key"

_SYSTEMD_RUN = """#!/bin/sh
dir="$(dirname "$0")"
{
  printf 'XDG=%s\\n' "$XDG_RUNTIME_DIR"
  printf 'NAMES=%s\\n' "$(env | cut -d= -f1 | sort | tr '\\n' ' ')"
  printf 'ARGS=%s\\n' "$*"
} > "$dir/seen.txt"
echo ran-under-stub
exit 0
"""


def _stub(directory: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> None:
    path = directory / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(
    not (Path("/run/user") / str(os.getuid())).is_dir(), reason="no /run/user/<uid> on this host"
)
async def test_run_python_via_manager_gets_xdg_runtime_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not runner._memory_delegated(Path("/"), os.getuid()):
        pytest.skip("memory cgroup controller not delegated on this host")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _stub(stubs, "systemd-run", _SYSTEMD_RUN)
    _stub(stubs, "bwrap")
    _stub(stubs, "prlimit")

    for key in list(os.environ):
        monkeypatch.delenv(key)
    monkeypatch.setenv("PATH", f"{stubs}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", _SECRET)
    monkeypatch.setenv("DEEPGRAM_API_KEY", _SECRET)

    spec = McpServerSpec(
        command=sys.executable,
        args=("-m", "voice_agent.mcp_servers.code", "--root", str(tmp_path / "box")),
        env={},
    )
    async with McpClientManager({"code": spec}, start_timeout_s=60) as reg:
        assert reg.unavailable == {}
        wrote = await reg.call("code__write_file", {"path": "hello.py", "content": "print(1)\n"})
        assert not wrote.is_error
        out = await reg.call("code__run_python", {"path": "hello.py"})

    assert not out.is_error, out.text
    assert json.loads(out.text)["stdout"].strip() == "ran-under-stub"
    seen_text = (stubs / "seen.txt").read_text()
    seen = dict(line.split("=", 1) for line in seen_text.splitlines() if "=" in line)
    assert seen["XDG"] == f"/run/user/{os.getuid()}"
    names = seen["NAMES"].split()
    assert "ANTHROPIC_API_KEY" not in names and "DEEPGRAM_API_KEY" not in names
    assert _SECRET not in seen_text
