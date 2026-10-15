from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from voice_agent.config import ConfigError, PathsConfig, load_config
from voice_agent.mcp_servers._paths import PathEscapeError, resolve_inside
from voice_agent.sandbox.runner import (
    SandboxPolicy,
    SandboxUnavailable,
    run_in_sandbox,
)

MAX_WRITE_BYTES = 256 * 1024
MAX_RUN_TIMEOUT_S = 120
MAX_SITE_FILES = 200
MAX_SITE_BYTES = 20 * 1024 * 1024
SITE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

log = logging.getLogger("voice_agent.mcp.code")
mcp = FastMCP("code")


@dataclass
class _State:
    root: Path
    sites_root: Path
    sites_host: str = "raspberrypi.local"
    sites_port: int = 8080
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state = _State(root=PathsConfig().sandbox_root, sites_root=PathsConfig().sites_root)


def _resolve(rel: str, *, must_exist: bool) -> Path:
    try:
        return resolve_inside(_state.root, rel, must_exist=must_exist)
    except PathEscapeError:
        raise ValueError("path outside sandbox root") from None
    except FileNotFoundError:
        raise ValueError(f"file not found: {rel}") from None


@mcp.tool()
async def write_file(path: str, content: str) -> dict[str, object]:
    """Write a text file under the sandbox folder. Returns {"path", "bytes"}."""
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(f"file too large: {len(data)} bytes exceeds {MAX_WRITE_BYTES}")
    dest = _resolve(path, must_exist=False)
    if dest == _state.root.resolve() or dest.is_dir():
        raise ValueError(f"path is a directory: {path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {"path": path, "bytes": len(data)}


@mcp.tool()
async def read_file(path: str) -> str:
    """Read a text file under the sandbox folder."""
    src = _resolve(path, must_exist=True)
    if not src.is_file():
        raise ValueError(f"not a file: {path}")
    if src.stat().st_size > MAX_WRITE_BYTES:
        raise ValueError(f"file too large to read: {path}")
    return src.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
async def list_files(path: str = ".") -> list[str]:
    """List entries of a folder under the sandbox folder; directories end with '/'."""
    folder = _resolve(path, must_exist=True)
    if not folder.is_dir():
        raise ValueError(f"not a directory: {path}")
    return sorted(f"{p.name}/" if p.is_dir() else p.name for p in folder.iterdir())


@mcp.tool()
async def run_python(
    path: str, args: list[str] | None = None, timeout_s: int = 60
) -> dict[str, object]:
    """Run a Python script from the sandbox folder in isolation (no network, 512M, 1 core)."""
    script = _resolve(path, must_exist=True)
    if not script.is_file():
        raise ValueError(f"not a file: {path}")
    rel = script.relative_to(_state.root.resolve()).as_posix()
    policy = SandboxPolicy(
        root=_state.root.resolve(), timeout_s=max(1, min(timeout_s, MAX_RUN_TIMEOUT_S))
    )
    async with _state.lock:
        try:
            result = await run_in_sandbox(policy, ["python3", f"/sandbox/{rel}", *(args or [])])
        except SandboxUnavailable as exc:
            raise ValueError(str(exc)) from None
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "oom_killed": result.oom_killed,
        "duration_s": result.duration_s,
    }


def _scan_site(src: Path) -> list[tuple[Path, Path, bool]]:
    entries: list[tuple[Path, Path, bool]] = []
    total = 0
    count = 0
    stack = [src]
    while stack:
        current = stack.pop()
        with os.scandir(current) as it:
            for entry in sorted(it, key=lambda e: e.name):
                full = Path(entry.path)
                rel = full.relative_to(src)
                st = os.lstat(full)
                if stat.S_ISDIR(st.st_mode):
                    entries.append((full, rel, True))
                    stack.append(full)
                elif stat.S_ISREG(st.st_mode):
                    count += 1
                    total += st.st_size
                    entries.append((full, rel, False))
                else:
                    kind = "symlink" if stat.S_ISLNK(st.st_mode) else "special file"
                    raise ValueError(f"cannot publish {kind}: {rel.as_posix()}")
    if count > MAX_SITE_FILES or total > MAX_SITE_BYTES:
        raise ValueError(
            f"site too large: {count} files, {total} bytes "
            f"(limits {MAX_SITE_FILES} files, {MAX_SITE_BYTES} bytes)"
        )
    return entries


@mcp.tool()
async def publish_site(subdir: str, name: str) -> dict[str, object]:
    """Publish a sandbox folder as a static website. Returns {"url", "files", "bytes"}."""
    if not SITE_NAME.fullmatch(name):
        raise ValueError("invalid site name: use 1-40 chars of a-z, 0-9 and '-', not starting '-'")
    src = _resolve(subdir, must_exist=True)
    if not src.is_dir():
        raise ValueError(f"not a directory: {subdir}")
    entries = _scan_site(src)
    sites = _state.sites_root
    if not sites.exists():
        sites.mkdir(mode=0o755, parents=True)
        sites.chmod(0o755)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=sites))
    old: Path | None = None
    try:
        tmp.chmod(0o755)
        files = 0
        size = 0
        for full, rel, is_dir in entries:
            target = tmp / rel
            if is_dir:
                target.mkdir(mode=0o755)
                continue
            shutil.copyfile(full, target, follow_symlinks=False)
            target.chmod(0o644)
            files += 1
            size += target.stat().st_size
        final = sites / name
        if final.exists():
            old = Path(tempfile.mkdtemp(prefix=".old-", dir=sites))
            final.rename(old / "site")
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    url = f"http://{_state.sites_host}:{_state.sites_port}/{name}/"
    return {"url": url, "files": files, "bytes": size}


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    parser = argparse.ArgumentParser(prog="voice_agent.mcp_servers.code")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
        paths = cfg.paths
        _state.sites_host, _state.sites_port = cfg.sites_host, cfg.sites_port
    except ConfigError:
        paths = PathsConfig()
    _state.root = (args.root or paths.sandbox_root).expanduser()
    _state.sites_root = paths.sites_root
    _state.root.mkdir(parents=True, exist_ok=True)
    log.info("code server: root=%s", _state.root)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
