from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from voice_agent.sandbox import runner
from voice_agent.sandbox.runner import (
    MAX_OUTPUT_BYTES,
    HostLayout,
    Preflight,
    SandboxPolicy,
    SandboxUnavailable,
    _read_capped,
    build_command,
    detect_layout,
    preflight,
    run_in_sandbox,
)

PREFIX = [
    "systemd-run", "--user", "--scope", "--quiet",
    "-p", "MemoryMax=512M", "-p", "CPUQuota=100%",
    "-p", "TasksMax=64", "-p", "MemorySwapMax=0",
    "timeout", "60", "bwrap",
]  # fmt: skip


def _fake_root(tmp_path: Path, *, lib64: str | None = None) -> Path:
    root = tmp_path / "host"
    for d in ("usr/bin", "usr/lib", "usr/sbin", "etc/alternatives"):
        (root / d).mkdir(parents=True)
    for name in ("bin", "lib", "sbin"):
        (root / name).symlink_to(f"usr/{name}")
    if lib64 == "dir":
        (root / "lib64").mkdir()
    elif lib64 == "symlink":
        (root / "usr/lib64").mkdir()
        (root / "lib64").symlink_to("usr/lib64")
    return root


def test_build_command_debian13_arm64(tmp_path: Path) -> None:
    layout = detect_layout(_fake_root(tmp_path))
    assert layout == HostLayout(
        usr_merged={"/bin": True, "/lib": True, "/sbin": True},
        has_lib64=False,
        has_etc_alternatives=True,
    )
    policy = SandboxPolicy(root=Path("/home/pi/agent_sandbox"))
    cmd = build_command(policy, ["python3", "s.py"], layout)
    assert cmd == [
        *PREFIX,
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/sbin", "/sbin",
        "--ro-bind", "/etc/alternatives", "/etc/alternatives",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--bind", "/home/pi/agent_sandbox", "/sandbox",
        "--chdir", "/sandbox", "--unshare-all", "--die-with-parent", "--new-session",
        "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/sandbox",
        "--setenv", "LANG", "C.UTF-8",
        "python3", "s.py",
    ]  # fmt: skip
    assert "/lib64" not in cmd


def test_build_command_with_lib64(tmp_path: Path) -> None:
    layout = detect_layout(_fake_root(tmp_path, lib64="dir"))
    assert layout.has_lib64
    cmd = build_command(SandboxPolicy(root=Path("/s")), ["x"], layout)
    i = cmd.index("/lib64")
    assert cmd[i - 1 : i + 2] == ["--ro-bind", "/lib64", "/lib64"]

    sym = detect_layout(_fake_root(tmp_path / "b", lib64="symlink"))
    cmd = build_command(SandboxPolicy(root=Path("/s")), ["x"], sym)
    i = cmd.index("/lib64")
    assert cmd[i - 2 : i + 1] == ["--symlink", "usr/lib64", "/lib64"]


def test_missing_and_real_dirs(tmp_path: Path) -> None:
    root = tmp_path / "old"
    (root / "usr").mkdir(parents=True)
    (root / "bin").mkdir()
    layout = detect_layout(root)
    assert layout.usr_merged == {"/bin": False}
    assert not layout.has_etc_alternatives
    cmd = build_command(SandboxPolicy(root=Path("/s")), ["x"], layout)
    assert cmd[cmd.index("/bin") - 1 : cmd.index("/bin") + 2] == ["--ro-bind", "/bin", "/bin"]
    assert "/lib" not in cmd and "/etc/alternatives" not in cmd


def test_network_flag_adds_share_net() -> None:
    layout = HostLayout({}, False, False)
    off = build_command(SandboxPolicy(root=Path("/s")), ["x"], layout)
    on = build_command(SandboxPolicy(root=Path("/s"), network=True), ["x"], layout)
    assert "--share-net" not in off
    assert on.index("--unshare-all") < on.index("--share-net") < on.index("--die-with-parent")


def test_ro_binds_and_limits() -> None:
    layout = HostLayout({}, False, False)
    policy = SandboxPolicy(
        root=Path("/s"), ro_binds=(Path("/opt/venv"),), memory_max="128M", timeout_s=5
    )
    cmd = build_command(policy, ["x"], layout)
    i = cmd.index("/opt/venv")
    assert cmd[i - 1 : i + 2] == ["--ro-bind", "/opt/venv", "/opt/venv"]
    assert "MemoryMax=128M" in cmd
    assert cmd[cmd.index("timeout") + 1] == "5"


def _executable(path: Path) -> None:
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)


def test_preflight_reports_missing_bwrap_and_memory(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    controllers = root / "sys/fs/cgroup/user.slice/cgroup.controllers"
    controllers.parent.mkdir(parents=True)
    controllers.write_text("cpu io pids\n")
    pf = preflight(root)
    assert pf.bwrap is None and pf.systemd_run is None
    assert not pf.memory_controller
    text = "\n".join(pf.problems)
    assert "bubblewrap" in text and "cgroup_enable=memory" in text

    _executable(root / "usr/bin/bwrap")
    _executable(root / "usr/bin/systemd-run")
    controllers.write_text("cpu memory pids\n")
    ok = preflight(root)
    assert ok.bwrap == root / "usr/bin/bwrap"
    assert ok.memory_controller
    assert ok.problems == []


async def test_output_truncation() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"a" * (MAX_OUTPUT_BYTES + 100))
    reader.feed_eof()
    assert await _read_capped(reader) == "a" * MAX_OUTPUT_BYTES + "…[truncated 100 bytes]"

    small = asyncio.StreamReader()
    small.feed_data(b"hi")
    small.feed_eof()
    assert await _read_capped(small) == "hi"


class _Fake:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, memory: bool = True) -> None:
        layout = HostLayout({}, False, False)
        ready = Preflight(Path("/x/bwrap"), Path("/x/systemd-run"), memory, layout, [])
        monkeypatch.setattr(runner, "preflight", lambda root=Path("/"), uid=None: ready)
        self.oom: list[int | None] = [0]
        monkeypatch.setattr(runner, "_oom_kill_count", lambda: self.oom.pop(0) if self.oom else 0)

    def command(self, monkeypatch: pytest.MonkeyPatch, shell: str, *, started: bool = True) -> None:
        def build(p: SandboxPolicy, a: object, layout: object) -> list[str]:
            marker = f"echo '{{}}' >&{p.info_fd}; " if started else ""
            return ["bash", "-c", marker + shell]

        monkeypatch.setattr(runner, "build_command", build)


async def test_run_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _Fake(monkeypatch)
    policy = SandboxPolicy(root=tmp_path, timeout_s=5)
    fake.command(monkeypatch, "echo out; echo err >&2; exit 3")
    res = await run_in_sandbox(policy, ["x"])
    assert (res.returncode, res.stdout, res.stderr) == (3, "out\n", "err\n")
    assert not res.timed_out and not res.oom_killed

    fake.command(monkeypatch, "exit 124")
    res = await run_in_sandbox(policy, ["x"])
    assert not res.timed_out and res.returncode == 124

    fake.command(monkeypatch, "exit 137")
    res = await run_in_sandbox(policy, ["x"])
    assert not res.oom_killed and res.returncode == 137

    fake.oom = [0, 1]
    fake.command(monkeypatch, "kill -9 $$")
    res = await run_in_sandbox(policy, ["x"])
    assert res.oom_killed and not res.timed_out

    fake.oom = [None, None]
    fake.command(monkeypatch, "kill -9 $$")
    assert (await run_in_sandbox(policy, ["x"])).oom_killed


async def test_run_real_timeout_exit_124_is_timed_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _Fake(monkeypatch).command(monkeypatch, "sleep 1; exit 124")
    res = await run_in_sandbox(SandboxPolicy(root=tmp_path, timeout_s=1), ["x"])
    assert res.timed_out and res.returncode == 124


async def test_child_cannot_forge_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _Fake(monkeypatch).command(
        monkeypatch, "echo 'Failed to connect to bus: forged' >&2; exit 1", started=True
    )
    res = await run_in_sandbox(SandboxPolicy(root=tmp_path), ["x"])
    assert res.returncode == 1 and "forged" in res.stderr


async def test_run_wall_clock_kill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "_KILL_GRACE_S", 0.2)
    _Fake(monkeypatch).command(monkeypatch, "sleep 30")
    res = await run_in_sandbox(SandboxPolicy(root=tmp_path, timeout_s=1), ["x"])
    assert res.timed_out and res.returncode == 124 and res.duration_s < 5


async def test_run_systemd_failure_and_memory_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Fake(monkeypatch)
    fake.command(
        monkeypatch, "echo 'Failed to connect to bus: No medium found' >&2; exit 1", started=False
    )
    with pytest.raises(SandboxUnavailable, match="Failed to connect to bus"):
        await run_in_sandbox(SandboxPolicy(root=tmp_path), ["x"])

    fake = _Fake(monkeypatch, memory=False)
    fake.command(monkeypatch, "echo hi")
    with pytest.raises(SandboxUnavailable, match="cgroup_enable=memory"):
        await run_in_sandbox(SandboxPolicy(root=tmp_path), ["x"])
    res = await run_in_sandbox(SandboxPolicy(root=tmp_path, allow_no_memory_limit=True), ["x"])
    assert res.stdout == "hi\n"


async def test_run_unavailable_without_bwrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    layout = HostLayout({}, False, False)
    pf = Preflight(None, Path("/x/systemd-run"), True, layout, ["bwrap not found"])
    monkeypatch.setattr(runner, "preflight", lambda root=Path("/"), uid=None: pf)
    with pytest.raises(SandboxUnavailable, match="install bubblewrap"):
        await run_in_sandbox(SandboxPolicy(root=tmp_path), ["x"])


async def test_run_scrubs_secret_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    _Fake(monkeypatch).command(monkeypatch, 'echo "[$ANTHROPIC_API_KEY] $HOME"')
    res = await run_in_sandbox(SandboxPolicy(root=tmp_path), ["x"])
    assert res.stdout == "[] /sandbox\n"


def test_new_flags_and_masks() -> None:
    layout = HostLayout({}, False, False)
    policy = SandboxPolicy(root=Path("/s"), hidden_paths=("media",), info_fd=7)
    cmd = build_command(policy, ["x"], layout)
    assert "TasksMax=64" in cmd and "MemorySwapMax=0" in cmd
    i = cmd.index("/sandbox/media")
    assert cmd[i - 1 : i + 1] == ["--tmpfs", "/sandbox/media"]
    assert cmd.index("/sandbox") < i < cmd.index("--chdir")
    assert cmd[cmd.index("--info-fd") + 1] == "7"
    j = cmd.index("--clearenv")
    assert cmd[j : j + 7] == [
        "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/sandbox"
    ]  # fmt: skip
    assert j < cmd.index("x", j)
    with pytest.raises(ValueError):
        build_command(SandboxPolicy(root=Path("/s"), hidden_paths=("../etc",)), ["x"], layout)


def test_preflight_uses_delegated_controllers(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    _executable(root / "usr/bin/bwrap")
    _executable(root / "usr/bin/systemd-run")
    slice_dir = root / "sys/fs/cgroup/user.slice"
    slice_dir.mkdir(parents=True)
    (slice_dir / "cgroup.controllers").write_text("cpu memory pids\n")
    deleg = slice_dir / "user-1000.slice/user@1000.service"
    deleg.mkdir(parents=True)

    (deleg / "cgroup.subtree_control").write_text("cpu pids\n")
    pf = preflight(root, uid=1000)
    assert not pf.memory_controller and any("cgroup_enable=memory" in p for p in pf.problems)

    (deleg / "cgroup.subtree_control").write_text("cpu memory pids\n")
    assert preflight(root, uid=1000).memory_controller

    assert preflight(root, uid=2000).memory_controller  # file absent: fall back to user.slice
    (slice_dir / "cgroup.controllers").write_text("cpu pids\n")
    assert not preflight(root, uid=2000).memory_controller


async def test_dir_usage_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _Fake(monkeypatch).command(monkeypatch, "head -c 300000 /dev/zero > big.bin")
    monkeypatch.chdir(tmp_path)
    res = await run_in_sandbox(SandboxPolicy(root=tmp_path, max_dir_bytes=100_000), ["x"])
    assert res.disk_exceeded and res.dir_bytes >= 300_000
    res = await run_in_sandbox(SandboxPolicy(root=tmp_path, max_dir_bytes=10_000_000), ["x"])
    assert not res.disk_exceeded
