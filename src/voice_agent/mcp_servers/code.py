from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
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
from typing import Any, BinaryIO

import anyio
from mcp.server.fastmcp import FastMCP

from voice_agent.config import ConfigError, PathsConfig, load_config
from voice_agent.mcp_servers._paths import (
    PathEscapeError,
    open_dir,
    open_regular_for_read,
    resolve_inside,
    write_file_atomic,
)
from voice_agent.mcp_servers.shell_policy import check_command
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
MAX_LIST_BYTES = 32 * 1024
MAX_GREP_MATCHES = 50
MAX_GREP_BYTES = 20 * 1024
MAX_GREP_LINE = 300
_TRUNC_MARK = "…[truncated"
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
    network: bool = False
    pylibs: Path | None = None
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
            names: list[str] = []
            used = 0
            truncated = False
            with os.scandir(fd) as it:
                for e in it:
                    shown = f"{e.name}/" if e.is_dir(follow_symlinks=False) else e.name
                    used += len(shown.encode("utf-8", errors="replace")) + 1
                    if len(names) >= MAX_LIST_ENTRIES or used > MAX_LIST_BYTES:
                        truncated = True
                        break
                    names.append(f"./{shown}" if shown.startswith(_TRUNC_MARK) else shown)
        finally:
            os.close(fd)
    result = sorted(names)
    if truncated:
        result.append(f"{_TRUNC_MARK}: more entries than the listing limit]")
    return result


def _media_unverifiable() -> ValueError:
    return ValueError("refusing to run: the media folder cannot be verified")


def _media_rel() -> str | None:
    media = _state.media_root
    if media is None:
        return None
    if ".." in media.parts:
        raise _media_unverifiable()
    try:
        lexical_media = Path(os.path.abspath(media))
        lexical_root = Path(os.path.abspath(_state.root))
        if lexical_media.is_relative_to(lexical_root):
            rel = lexical_media.relative_to(lexical_root)
            if rel.parts:
                return rel.as_posix()
        real_media, real_root = lexical_media.resolve(), _state.root.resolve()
    except (OSError, RuntimeError):
        raise _media_unverifiable() from None
    if real_media.is_relative_to(real_root):
        rel = real_media.relative_to(real_root)
        if rel.parts:
            return rel.as_posix()
    return None


def _hidden_has_symlink(real_root: Path, hidden: str) -> bool:
    parts = Path(hidden).parts
    try:
        return any(real_root.joinpath(*parts[: i + 1]).is_symlink() for i in range(len(parts)))
    except (OSError, RuntimeError):
        raise _media_unverifiable() from None


@mcp.tool()
async def run_python(
    path: str, args: list[str] | None = None, timeout_s: int = 60
) -> dict[str, object]:
    """Run a Python script from the sandbox folder in isolation (512M, 1 core; --network only)."""
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
            host_readonly=_state.network,
            **_extras(),
        )
        try:
            result = await run_in_sandbox(policy, ["python3", f"/sandbox/{rel}", *(args or [])])
        except SandboxUnavailable as exc:
            raise ValueError(str(exc)) from None
    if result.disk_exceeded:
        raise ValueError(
            f"sandbox folder is over the limit: at least {result.dir_bytes} bytes used, "
            f"limit {policy.max_dir_bytes}; shrink large files by overwriting them with "
            "write_file (list_files shows what exists), then retry"
        )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "oom_killed": result.oom_killed,
        "duration_s": result.duration_s,
    }


GIT_IDENTITY = (
    ("GIT_AUTHOR_NAME", "Voice Agent"),
    ("GIT_COMMITTER_NAME", "Voice Agent"),
    ("GIT_AUTHOR_EMAIL", "voice-agent@localhost"),
    ("GIT_COMMITTER_EMAIL", "voice-agent@localhost"),
    ("GIT_TERMINAL_PROMPT", "0"),  # a missing credential fails at once instead of waiting
)


def _extras() -> dict[str, Any]:
    """Policy fields: a git identity always; --network / --pylibs (delegate jobs) when set."""
    extras: dict[str, Any] = {"network": _state.network, "env": GIT_IDENTITY}
    libs = _state.pylibs
    if libs is not None and libs.is_dir():
        env = (
            ("PYTHONPATH", str(libs)),
            ("SDL_VIDEODRIVER", "dummy"),
            ("SDL_AUDIODRIVER", "dummy"),
            ("PYGAME_HIDE_SUPPORT_PROMPT", "1"),
        )
        extras |= {"ro_binds": (libs,), "env": env + GIT_IDENTITY}
    return extras


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
        **_extras(),
    )


@mcp.tool()
async def bash(command: str, timeout_s: int = 60) -> dict[str, object]:
    """Run a shell command in the isolated sandbox.

    The machine is visible read-only, only the working folder is writable, there is no network
    unless the server was started with --network, and sudo, ssh and similar commands are refused.
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
        task = asyncio.ensure_future(asyncio.to_thread(_publish, subdir, name))
        try:
            return await asyncio.shield(task)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await asyncio.wait({task})
            if not task.cancelled():
                task.exception()
            raise


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
    parser.add_argument(
        "--network", action="store_true", help="let sandboxed commands use the network"
    )
    parser.add_argument("--pylibs", type=Path, help="read-only folder of extra Python packages")
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
        paths = cfg.paths
        _state.sites_host, _state.sites_port = cfg.sites_host, cfg.sites_port
    except ConfigError:
        paths = PathsConfig()
    _state.root = (args.root or paths.sandbox_root).expanduser()
    _state.network = args.network
    _state.pylibs = args.pylibs.expanduser().resolve() if args.pylibs else None
    _state.sites_root = paths.sites_root
    _state.media_root = paths.media_root.expanduser()
    if ".." in _state.media_root.parts:
        parser.error("media_root must not contain '..' components")
    _state.root.mkdir(parents=True, exist_ok=True)
    log.info("code server: root=%s", _state.root)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
