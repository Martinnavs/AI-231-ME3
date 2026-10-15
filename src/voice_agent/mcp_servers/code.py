from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import itertools
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
from typing import BinaryIO

from mcp.server.fastmcp import FastMCP

from voice_agent.config import ConfigError, PathsConfig, load_config
from voice_agent.mcp_servers._paths import (
    PathEscapeError,
    open_dir,
    open_regular_for_read,
    resolve_inside,
    write_file_atomic,
)
from voice_agent.sandbox.runner import (
    SandboxPolicy,
    SandboxUnavailable,
    run_in_sandbox,
)

MAX_WRITE_BYTES = 256 * 1024
MAX_RUN_TIMEOUT_S = 120
MAX_SITE_FILES = 200
MAX_SITE_BYTES = 20 * 1024 * 1024
MAX_SITE_DIRS = 100
MAX_SITE_DEPTH = 8
MAX_LIST_ENTRIES = 1000
SITE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

log = logging.getLogger("voice_agent.mcp.code")
mcp = FastMCP("code")


@dataclass
class _State:
    root: Path
    sites_root: Path
    sites_host: str = "raspberrypi.local"
    sites_port: int = 8080
    media_root: Path | None = field(default_factory=lambda: PathsConfig().media_root)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state = _State(root=PathsConfig().sandbox_root, sites_root=PathsConfig().sites_root)


def _escape_error() -> ValueError:
    return ValueError("path outside sandbox root")


def _resolve(rel: str, *, must_exist: bool) -> Path:
    try:
        return resolve_inside(_state.root, rel, must_exist=must_exist)
    except PathEscapeError:
        raise _escape_error() from None
    except FileNotFoundError:
        raise ValueError(f"file not found: {rel}") from None


@mcp.tool()
async def write_file(path: str, content: str) -> dict[str, object]:
    """Write a text file under the sandbox folder. Returns {"path", "bytes"}."""
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(f"file too large: {len(data)} bytes exceeds {MAX_WRITE_BYTES}")

    def writer(fh: BinaryIO) -> None:
        fh.write(data)

    async with _state.lock:
        try:
            write_file_atomic(_state.root, path, writer, overwrite=True)
        except PathEscapeError:
            raise _escape_error() from None
        except IsADirectoryError:
            raise ValueError(f"path is a directory: {path}") from None
    return {"path": path, "bytes": len(data)}


@mcp.tool()
async def read_file(path: str) -> str:
    """Read a text file under the sandbox folder."""
    async with _state.lock:
        try:
            with open_regular_for_read(_state.root, path) as fh:
                if os.fstat(fh.fileno()).st_size > MAX_WRITE_BYTES:
                    raise ValueError(f"file too large to read: {path}")
                data = fh.read(MAX_WRITE_BYTES + 1)
        except PathEscapeError:
            raise _escape_error() from None
        except FileNotFoundError:
            raise ValueError(f"file not found: {path}") from None
        except IsADirectoryError:
            raise ValueError(f"not a file: {path}") from None
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(f"file too large to read: {path}")
    return data.decode("utf-8", errors="replace")


@mcp.tool()
async def list_files(path: str = ".") -> list[str]:
    """List entries of a folder under the sandbox folder; directories end with '/'."""
    async with _state.lock:
        try:
            fd = open_dir(_state.root, path)
        except PathEscapeError:
            raise _escape_error() from None
        except FileNotFoundError:
            raise ValueError(f"file not found: {path}") from None
        except NotADirectoryError:
            raise ValueError(f"not a directory: {path}") from None
        try:
            with os.scandir(fd) as it:
                names = [
                    f"{e.name}/" if e.is_dir(follow_symlinks=False) else e.name
                    for e in itertools.islice(it, MAX_LIST_ENTRIES + 1)
                ]
        finally:
            os.close(fd)
    truncated = len(names) > MAX_LIST_ENTRIES
    result = sorted(names[:MAX_LIST_ENTRIES])
    if truncated:
        result.append(f"…[truncated: more than {MAX_LIST_ENTRIES} entries]")
    return result


def _media_rel() -> str | None:
    media = _state.media_root
    if media is None:
        return None
    lexical_media = Path(os.path.abspath(media))
    candidates = (
        (lexical_media, Path(os.path.abspath(_state.root))),
        (lexical_media.resolve(), _state.root.resolve()),
    )
    for m, r in candidates:
        if m.is_relative_to(r):
            rel = m.relative_to(r)
            if rel.parts:
                return rel.as_posix()
    return None


def _hidden_has_symlink(real_root: Path, hidden: str) -> bool:
    parts = Path(hidden).parts
    return any((real_root.joinpath(*parts[: i + 1])).is_symlink() for i in range(len(parts)))


@mcp.tool()
async def run_python(
    path: str, args: list[str] | None = None, timeout_s: int = 60
) -> dict[str, object]:
    """Run a Python script from the sandbox folder in isolation (no network, 512M, 1 core)."""
    async with _state.lock:
        script = _resolve(path, must_exist=True)
        if not script.is_file():
            raise ValueError(f"not a file: {path}")
        real_root = _state.root.resolve()
        rel = script.relative_to(real_root).as_posix()
        hidden = _media_rel()
        if hidden is not None and _hidden_has_symlink(real_root, hidden):
            raise ValueError(f"refusing to run: {hidden} inside the sandbox is a symlink")
        policy = SandboxPolicy(
            root=real_root,
            timeout_s=max(1, min(timeout_s, MAX_RUN_TIMEOUT_S)),
            hidden_paths=(hidden,) if hidden else (),
        )
        try:
            result = await run_in_sandbox(policy, ["python3", f"/sandbox/{rel}", *(args or [])])
        except SandboxUnavailable as exc:
            raise ValueError(str(exc)) from None
    if result.disk_exceeded:
        raise ValueError(
            f"sandbox folder is over the limit: {result.dir_bytes} bytes used, "
            f"limit {policy.max_dir_bytes}; delete files and retry"
        )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "oom_killed": result.oom_killed,
        "duration_s": result.duration_s,
    }


@dataclass
class _Tally:
    files: int = 0
    dirs: int = 0
    bytes: int = 0


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_NEW_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _too_large(tally: _Tally) -> ValueError:
    return ValueError(
        f"site too large: {tally.files} files, {tally.bytes} bytes "
        f"(limits {MAX_SITE_FILES} files, {MAX_SITE_BYTES} bytes)"
    )


def _copy_file(src_fd: int, dst_fd: int, name: str, rel: str, tally: _Tally) -> None:
    try:
        fd = os.open(name, _OPEN_FLAGS, dir_fd=src_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"cannot publish symlink: {rel}") from None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"cannot publish special file: {rel}")
        tally.files += 1
        if tally.files > MAX_SITE_FILES:
            raise _too_large(tally)
        out = os.open(name, _NEW_FLAGS, 0o600, dir_fd=dst_fd)
        try:
            while chunk := os.read(fd, 65536):
                tally.bytes += len(chunk)
                if tally.bytes > MAX_SITE_BYTES:
                    raise _too_large(tally)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(out, view) :]
            os.fchmod(out, 0o644)
        finally:
            os.close(out)
    finally:
        os.close(fd)


def _copy_tree(src_fd: int, dst_fd: int, prefix: str, depth: int, tally: _Tally) -> None:
    with os.scandir(src_fd) as it:
        entries = sorted(
            (
                e.name,
                e.is_symlink(),
                e.is_dir(follow_symlinks=False),
                e.is_file(follow_symlinks=False),
            )
            for e in it
            if not e.name.startswith(".")
        )
    for name, is_link, is_dir, is_file in entries:
        if name.startswith("."):
            continue
        rel = f"{prefix}{name}"
        if is_link:
            raise ValueError(f"cannot publish symlink: {rel}")
        if is_file:
            _copy_file(src_fd, dst_fd, name, rel, tally)
            continue
        if not is_dir:
            raise ValueError(f"cannot publish special file: {rel}")
        if depth + 1 > MAX_SITE_DEPTH:
            raise ValueError(f"site too deep: {rel} exceeds {MAX_SITE_DEPTH} levels")
        tally.dirs += 1
        if tally.dirs > MAX_SITE_DIRS:
            raise ValueError(f"site has too many directories (limit {MAX_SITE_DIRS})")
        try:
            sub_src = os.open(name, _DIR_FLAGS, dir_fd=src_fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError(f"cannot publish symlink: {rel}") from None
            raise
        try:
            os.mkdir(name, 0o700, dir_fd=dst_fd)
            sub_dst = os.open(name, _DIR_FLAGS, dir_fd=dst_fd)
            try:
                _copy_tree(sub_src, sub_dst, f"{rel}/", depth + 1, tally)
                os.fchmod(sub_dst, 0o755)
            finally:
                os.close(sub_dst)
        finally:
            os.close(sub_src)


def _overlaps_media(src: Path) -> bool:
    media = _state.media_root
    if media is None:
        return False
    real_media = Path(os.path.abspath(media)).resolve()
    return src == real_media or src.is_relative_to(real_media) or real_media.is_relative_to(src)


@mcp.tool()
async def publish_site(subdir: str, name: str) -> dict[str, object]:
    """Publish a sandbox folder as a static website. Returns {"url", "files", "bytes"}."""
    if not SITE_NAME.fullmatch(name):
        raise ValueError("invalid site name: use 1-40 chars of a-z, 0-9 and '-', not starting '-'")
    async with _state.lock:
        return await asyncio.to_thread(_publish, subdir, name)


def _publish(subdir: str, name: str) -> dict[str, object]:
    src = _resolve(subdir, must_exist=True)
    if src == _state.root.resolve():
        raise ValueError("cannot publish the whole sandbox folder: name a subfolder")
    if _overlaps_media(src):
        raise ValueError("cannot publish a folder that overlaps the media folder")
    try:
        src_fd = open_dir(_state.root, subdir)
    except PathEscapeError:
        raise _escape_error() from None
    except NotADirectoryError:
        raise ValueError(f"not a directory: {subdir}") from None
    sites = _state.sites_root
    created_sites = not sites.exists()
    final = sites / name
    tmp: Path | None = None
    backup_dir: Path | None = None
    tally = _Tally()
    try:
        if created_sites:
            sites.mkdir(mode=0o755, parents=True)
            sites.chmod(0o755)
        tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=sites))
        dst_fd = os.open(tmp, _DIR_FLAGS)
        try:
            _copy_tree(src_fd, dst_fd, "", 0, tally)
            os.fchmod(dst_fd, 0o755)
        finally:
            os.close(dst_fd)
        backup: Path | None = None
        if final.exists():
            backup_dir = Path(tempfile.mkdtemp(prefix=".old-", dir=sites))
            backup = backup_dir / "site"
            final.rename(backup)
        try:
            tmp.rename(final)
        except BaseException:
            if backup is not None:
                backup.rename(final)
            raise
        tmp = None
    except BaseException:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        if created_sites:
            with contextlib.suppress(OSError):
                sites.rmdir()
        raise
    finally:
        os.close(src_fd)
        if backup_dir is not None:
            shutil.rmtree(backup_dir, ignore_errors=True)
    url = f"http://{_state.sites_host}:{_state.sites_port}/{name}/"
    return {"url": url, "files": tally.files, "bytes": tally.bytes}


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
    _state.media_root = paths.media_root.expanduser()
    _state.root.mkdir(parents=True, exist_ok=True)
    log.info("code server: root=%s", _state.root)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
