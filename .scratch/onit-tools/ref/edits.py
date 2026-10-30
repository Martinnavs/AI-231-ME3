# (file, old, new): each old block must occur exactly once in the file as it is when applied.
EDITS = [
    (
        "src/voice_agent/sandbox/runner.py",
        '_MAX_SCAN_ENTRIES = 200_000\n',
        '_MAX_SCAN_ENTRIES = 200_000\n_HOST_RO_DIRS = ("/etc", "/opt", "/srv", "/var")\n',
    ),
    (
        "src/voice_agent/sandbox/runner.py",
        '    info_fd: int | None = None\n\n\n@dataclass(frozen=True)\nclass HostLayout:',
        '    info_fd: int | None = None\n    host_readonly: bool = False\n\n\n@dataclass(frozen=True)\nclass HostLayout:',
    ),
    (
        "src/voice_agent/sandbox/runner.py",
        '    if layout.has_etc_alternatives:\n        cmd += ["--ro-bind", "/etc/alternatives", "/etc/alternatives"]\n',
        '    if policy.host_readonly:\n        for host in _HOST_RO_DIRS:\n            if Path(host).is_dir():\n                cmd += ["--ro-bind", host, host]\n    elif layout.has_etc_alternatives:\n        cmd += ["--ro-bind", "/etc/alternatives", "/etc/alternatives"]\n',
    ),
    (
        "src/voice_agent/mcp_servers/code.py",
        'from voice_agent.sandbox.runner import (',
        'from voice_agent.mcp_servers.shell_policy import check_command\nfrom voice_agent.sandbox.runner import (',
    ),
    (
        "src/voice_agent/mcp_servers/code.py",
        'MAX_LIST_BYTES = 32 * 1024\n',
        'MAX_LIST_BYTES = 32 * 1024\nMAX_GREP_MATCHES = 50\nMAX_GREP_BYTES = 20 * 1024\nMAX_GREP_LINE = 300\n',
    ),
    (
        "src/voice_agent/mcp_servers/code.py",
        '''@dataclass
class _Tally:''',
        '''def _policy(timeout_s: int, *, host_readonly: bool = False) -> SandboxPolicy:
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
    """Run a shell command in the isolated sandbox.\n\n    The machine is visible read-only, only the working folder is writable, there is no network,\n    and sudo, ssh and similar commands are refused.\n    """
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
    return "\\n".join(lines[:MAX_GREP_MATCHES])[:MAX_GREP_BYTES]


@dataclass
class _Tally:''',
    ),
]
