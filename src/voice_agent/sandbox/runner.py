from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT_BYTES = 64 * 1024
_KILL_GRACE_S = 5
_SYSTEMD_FAILURES = ("Failed to connect to", "Failed to create bus", "Failed to start transient")
_PASS_ENV = ("PATH", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
_MERGED_DIRS = ("/bin", "/lib", "/sbin")


@dataclass(frozen=True)
class SandboxPolicy:
    root: Path
    memory_max: str = "512M"
    cpu_quota: str = "100%"
    timeout_s: int = 60
    network: bool = False
    ro_binds: tuple[Path, ...] = ()
    allow_no_memory_limit: bool = False


@dataclass(frozen=True)
class HostLayout:
    # value True: symlink into /usr; False: real directory; key absent: path missing
    usr_merged: dict[str, bool]
    has_lib64: bool
    has_etc_alternatives: bool


@dataclass(frozen=True)
class Preflight:
    bwrap: Path | None
    systemd_run: Path | None
    memory_controller: bool
    layout: HostLayout
    problems: list[str]


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    oom_killed: bool
    duration_s: float


class SandboxUnavailable(RuntimeError):
    pass


def _points_into_usr(path: Path) -> bool:
    if not path.is_symlink():
        return False
    return Path(os.path.realpath(path)).is_relative_to(path.parent.resolve() / "usr")


def detect_layout(root: Path = Path("/")) -> HostLayout:
    merged: dict[str, bool] = {}
    for name in _MERGED_DIRS:
        p = root / name.lstrip("/")
        if p.is_symlink() and _points_into_usr(p):
            merged[name] = True
        elif p.is_dir() and not p.is_symlink():
            merged[name] = False
    lib64 = root / "lib64"
    if lib64.is_symlink() and _points_into_usr(lib64):
        merged["/lib64"] = True
    return HostLayout(
        usr_merged=merged,
        has_lib64=lib64.is_dir() and not lib64.is_symlink(),
        has_etc_alternatives=(root / "etc/alternatives").is_dir(),
    )


def _which(name: str, root: Path) -> Path | None:
    if root == Path("/"):
        found = shutil.which(name)
        return Path(found) if found else None
    search = os.pathsep.join(str(root / d) for d in ("usr/bin", "bin", "usr/sbin", "sbin"))
    found = shutil.which(name, path=search)
    return Path(found) if found else None


def preflight(root: Path = Path("/")) -> Preflight:
    bwrap = _which("bwrap", root)
    systemd_run = _which("systemd-run", root)
    controllers = root / "sys/fs/cgroup/user.slice/cgroup.controllers"
    try:
        memory = "memory" in controllers.read_text().split()
    except OSError:
        memory = False
    problems: list[str] = []
    if bwrap is None:
        problems.append("bwrap not found: install bubblewrap")
    if systemd_run is None:
        problems.append("systemd-run not found")
    if not memory:
        problems.append(
            "memory cgroup controller missing from user.slice: add cgroup_enable=memory to "
            "/boot/firmware/cmdline.txt and reboot"
        )
    return Preflight(bwrap, systemd_run, memory, detect_layout(root), problems)


def build_command(policy: SandboxPolicy, argv: Sequence[str], layout: HostLayout) -> list[str]:
    cmd = [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        f"MemoryMax={policy.memory_max}",
        "-p",
        f"CPUQuota={policy.cpu_quota}",
        "timeout",
        str(policy.timeout_s),
        "bwrap",
        "--ro-bind",
        "/usr",
        "/usr",
    ]
    for name in (*_MERGED_DIRS, "/lib64"):
        merged = layout.usr_merged.get(name)
        if merged:
            cmd += ["--symlink", f"usr{name}", name]
        elif merged is False:
            cmd += ["--ro-bind", name, name]
    if layout.has_lib64 and "/lib64" not in layout.usr_merged:
        cmd += ["--ro-bind", "/lib64", "/lib64"]
    if layout.has_etc_alternatives:
        cmd += ["--ro-bind", "/etc/alternatives", "/etc/alternatives"]
    cmd += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    cmd += ["--bind", str(policy.root), "/sandbox"]
    for ro in policy.ro_binds:
        cmd += ["--ro-bind", str(ro), str(ro)]
    cmd += ["--chdir", "/sandbox", "--unshare-all"]
    if policy.network:
        cmd.append("--share-net")
    cmd += ["--die-with-parent", "--new-session", *argv]
    return cmd


def _scrubbed_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
    env["HOME"] = "/sandbox"
    return env


async def _read_capped(stream: asyncio.StreamReader) -> str:
    kept = bytearray()
    total = 0
    while chunk := await stream.read(65536):
        total += len(chunk)
        room = MAX_OUTPUT_BYTES - len(kept)
        if room > 0:
            kept += chunk[:room]
    text = bytes(kept).decode("utf-8", errors="replace")
    if total > MAX_OUTPUT_BYTES:
        text += f"…[truncated {total - MAX_OUTPUT_BYTES} bytes]"
    return text


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


async def run_in_sandbox(policy: SandboxPolicy, argv: Sequence[str]) -> SandboxResult:
    pre = preflight()
    if pre.bwrap is None:
        raise SandboxUnavailable("sandbox unavailable: install bubblewrap")
    if pre.systemd_run is None:
        raise SandboxUnavailable("sandbox unavailable: systemd-run not found")
    if not pre.memory_controller and not policy.allow_no_memory_limit:
        raise SandboxUnavailable(
            "sandbox unavailable: memory cgroup controller missing; add cgroup_enable=memory "
            "to /boot/firmware/cmdline.txt and reboot"
        )
    cmd = build_command(policy, argv, pre.layout)
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_scrubbed_env(),
        start_new_session=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    readers = asyncio.gather(_read_capped(proc.stdout), _read_capped(proc.stderr))
    wall_clock_hit = False
    try:
        await asyncio.wait_for(proc.wait(), policy.timeout_s + _KILL_GRACE_S)
    except TimeoutError:
        wall_clock_hit = True
        _kill_group(proc)
        await proc.wait()
    except BaseException:
        _kill_group(proc)
        raise
    stdout, stderr = await readers
    returncode = proc.returncode if proc.returncode is not None else -1
    if returncode != 0 and stderr.startswith(_SYSTEMD_FAILURES):
        raise SandboxUnavailable(f"sandbox unavailable: {stderr.strip()[:300]}")
    timed_out = wall_clock_hit or returncode == 124
    if wall_clock_hit:
        returncode = 124
    oom = not timed_out and returncode in (137, -signal.SIGKILL)
    return SandboxResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        oom_killed=oom,
        duration_s=time.monotonic() - start,
    )
