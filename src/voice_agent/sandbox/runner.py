from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import anyio

MAX_OUTPUT_BYTES = 64 * 1024
_KILL_GRACE_S = 5
_SYSTEMD_FAILURES = ("Failed to connect to", "Failed to create bus", "Failed to start transient")
_PASS_ENV = ("PATH", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
_MERGED_DIRS = ("/bin", "/lib", "/sbin")
_RUN_USER = Path("/run/user")
_MAX_SCAN_ENTRIES = 200_000
_HOST_RO_DIRS = ("/etc", "/opt", "/srv", "/var")


@dataclass(frozen=True)
class SandboxPolicy:
    root: Path
    memory_max: str = "512M"
    cpu_quota: str = "100%"
    timeout_s: int = 60
    network: bool = False
    ro_binds: tuple[Path, ...] = ()
    allow_no_memory_limit: bool = False
    hidden_paths: tuple[str, ...] = ()  # root-relative paths masked with an empty tmpfs
    max_dir_bytes: int = 256 * 1024 * 1024
    info_fd: int | None = None
    host_readonly: bool = False


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
    dir_bytes: int = 0
    disk_exceeded: bool = False


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


def _memory_delegated(root: Path, uid: int) -> bool:
    cgroup = root / "sys/fs/cgroup/user.slice"
    delegated = cgroup / f"user-{uid}.slice/user@{uid}.service/cgroup.subtree_control"
    for path in (delegated, cgroup / "cgroup.controllers"):
        try:
            return "memory" in path.read_text().split()
        except OSError:
            continue
    return False


def preflight(root: Path = Path("/"), uid: int | None = None) -> Preflight:
    bwrap = _which("bwrap", root)
    systemd_run = _which("systemd-run", root)
    memory = _memory_delegated(root, os.getuid() if uid is None else uid)
    problems: list[str] = []
    if bwrap is None:
        problems.append("bwrap not found: install bubblewrap")
    if systemd_run is None:
        problems.append("systemd-run not found")
    if _which("prlimit", root) is None:
        problems.append("prlimit not found: install util-linux")
    if not memory:
        problems.append(
            "memory cgroup controller not delegated to the user manager: add "
            "cgroup_enable=memory to /boot/firmware/cmdline.txt and reboot"
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
        "-p",
        "TasksMax=64",
        "-p",
        "MemorySwapMax=0",
        "timeout",
        str(policy.timeout_s),
        "prlimit",
        f"--fsize={policy.max_dir_bytes}",
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
    if policy.host_readonly:
        for host in _HOST_RO_DIRS:
            if Path(host).is_dir():
                cmd += ["--ro-bind", host, host]
    elif layout.has_etc_alternatives:
        cmd += ["--ro-bind", "/etc/alternatives", "/etc/alternatives"]
    cmd += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    cmd += ["--bind", str(policy.root), "/sandbox"]
    for ro in policy.ro_binds:
        cmd += ["--ro-bind", str(ro), str(ro)]
    for hidden in policy.hidden_paths:
        parts = hidden.split("/")
        if hidden.startswith("/") or ".." in parts or not hidden:
            raise ValueError(f"hidden path must be relative and inside the root: {hidden!r}")
        cmd += ["--tmpfs", f"/sandbox/{hidden}"]
    cmd += ["--chdir", "/sandbox", "--unshare-all"]
    if policy.network:
        cmd.append("--share-net")
    if policy.info_fd is not None:
        cmd += ["--info-fd", str(policy.info_fd)]
    cmd += ["--die-with-parent", "--new-session"]
    cmd += ["--clearenv", "--setenv", "PATH", "/usr/bin:/bin"]
    cmd += ["--setenv", "HOME", "/sandbox", "--setenv", "LANG", "C.UTF-8"]
    cmd += list(argv)
    return cmd


def _scrubbed_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
    env["HOME"] = "/sandbox"
    if "XDG_RUNTIME_DIR" not in env:
        uid = os.getuid()
        runtime = _RUN_USER / str(uid)
        with contextlib.suppress(OSError):
            if runtime.is_dir() and runtime.stat().st_uid == uid:
                env["XDG_RUNTIME_DIR"] = str(runtime)
    runtime_dir = env.get("XDG_RUNTIME_DIR")
    if runtime_dir and "DBUS_SESSION_BUS_ADDRESS" not in env:
        bus = Path(runtime_dir) / "bus"
        with contextlib.suppress(OSError):
            if bus.is_socket():
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
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


def _oom_kill_count(uid: int | None = None) -> int | None:
    uid = os.getuid() if uid is None else uid
    path = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/memory.events")
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == "oom_kill":
                return int(value)
    except (OSError, ValueError):
        pass
    return None


def _dir_usage(root: Path, limit: int) -> int:
    """Measured bytes (a lower bound once past ``limit`` or when the scan is incomplete).

    Never invents a number: unreadable subtrees and entry-cap overflow are skipped, so a script
    cannot lock the sandbox out. Directories we own that lack u+rwx are repaired so they count.
    """
    total = 0
    seen = 0
    uid = os.getuid()
    stack = [str(root)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    seen += 1
                    if seen > _MAX_SCAN_ENTRIES:
                        return total
                    st = entry.stat(follow_symlinks=False)
                    total += st.st_blocks * 512
                    if total > limit:
                        return total
                    if stat.S_ISDIR(st.st_mode):
                        if st.st_uid == uid and st.st_mode & 0o700 != 0o700:
                            with contextlib.suppress(OSError):
                                os.chmod(entry.path, st.st_mode | 0o700)
                        stack.append(entry.path)
        except OSError:
            continue
    return total


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
    before = await asyncio.to_thread(_dir_usage, policy.root, policy.max_dir_bytes)
    if before > policy.max_dir_bytes:
        return SandboxResult(
            returncode=1,
            stdout="",
            stderr="",
            timed_out=False,
            oom_killed=False,
            duration_s=0.0,
            dir_bytes=before,
            disk_exceeded=True,
        )
    info_r, info_w = os.pipe()
    os.set_blocking(info_r, False)
    cmd = build_command(replace(policy, info_fd=info_w), argv, pre.layout)
    oom_before = _oom_kill_count()
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_scrubbed_env(),
            start_new_session=True,
            pass_fds=(info_w,),
        )
    except BaseException:
        os.close(info_r)
        raise
    finally:
        os.close(info_w)
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
        os.close(info_r)
        readers.cancel()
        with anyio.CancelScope(shield=True):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await readers
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(asyncio.wait_for(proc.wait(), _KILL_GRACE_S))
        raise
    stdout, stderr = await readers
    duration = time.monotonic() - start
    try:
        started = bool(os.read(info_r, 1))
    except OSError:
        started = False
    finally:
        os.close(info_r)
    returncode = proc.returncode if proc.returncode is not None else -1
    if not started and returncode != 0 and stderr.startswith(_SYSTEMD_FAILURES):
        raise SandboxUnavailable(f"sandbox unavailable: {stderr.strip()[:300]}")
    timed_out = wall_clock_hit or (returncode == 124 and started and duration >= policy.timeout_s)
    if wall_clock_hit:
        returncode = 124
    oom = False
    if not timed_out and returncode in (137, -signal.SIGKILL):
        oom_after = _oom_kill_count()
        oom = oom_before is None or oom_after is None or oom_after > oom_before
    dir_bytes = await asyncio.to_thread(_dir_usage, policy.root, policy.max_dir_bytes)
    return SandboxResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        oom_killed=oom,
        duration_s=duration,
        dir_bytes=dir_bytes,
        disk_exceeded=dir_bytes > policy.max_dir_bytes,
    )
