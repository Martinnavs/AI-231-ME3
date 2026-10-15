from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO


class PathEscapeError(ValueError):
    pass


def resolve_inside(root: Path, rel: str, *, must_exist: bool) -> Path:
    if not rel or "\x00" in rel:
        raise PathEscapeError("path outside media root")
    if PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).anchor:
        raise PathEscapeError("path outside media root")
    if ".." in rel.replace("\\", "/").split("/"):
        raise PathEscapeError("path outside media root")
    if root.is_symlink():
        raise PathEscapeError("path outside media root")
    real_root = root.resolve()
    try:
        resolved = (real_root / rel).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathEscapeError("path outside media root") from exc
    if resolved != real_root and not resolved.is_relative_to(real_root):
        raise PathEscapeError("path outside media root")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"file not found: {rel}")
    return resolved


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _escape() -> PathEscapeError:
    return PathEscapeError("path outside media root")


def _check_fd_inside(fd: int, real_root: Path) -> None:
    link = f"/proc/self/fd/{fd}"
    if not os.path.exists(link):
        return
    if not Path(os.path.realpath(link)).is_relative_to(real_root):
        raise _escape()


def _open_parent(root: Path, rel: str, *, create: bool) -> tuple[int, str]:
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        raise _escape()
    real_root = root.resolve()
    fd = os.open(real_root, _DIR_FLAGS)
    try:
        for part in parts[:-1]:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise _escape() from exc
                raise
            os.close(fd)
            fd = nxt
        _check_fd_inside(fd, real_root)
    except BaseException:
        os.close(fd)
        raise
    return fd, parts[-1]


def open_dir(root: Path, rel: str) -> int:
    resolve_inside(root, rel, must_exist=False)
    if not [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]:
        return os.open(root.resolve(), _DIR_FLAGS)
    dfd, name = _open_parent(root, rel, create=False)
    try:
        try:
            return os.open(name, _DIR_FLAGS, dir_fd=dfd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _escape() from exc
            raise
    finally:
        os.close(dfd)


def open_regular_for_read(root: Path, rel: str) -> BinaryIO:
    resolve_inside(root, rel, must_exist=False)
    dfd, name = _open_parent(root, rel, create=False)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _escape() from exc
            raise
    finally:
        os.close(dfd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IsADirectoryError(rel)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def write_file_atomic(
    root: Path,
    rel: str,
    writer: Callable[[BinaryIO], None],
    *,
    overwrite: bool,
) -> None:
    resolve_inside(root, rel, must_exist=False)
    dfd, name = _open_parent(root, rel, create=True)
    tmp = f".{name}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(
            tmp,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=dfd,
        )
        try:
            with os.fdopen(fd, "w+b") as fh:
                writer(fh)
            if overwrite:
                os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            else:
                os.link(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp, dir_fd=dfd)
    finally:
        os.close(dfd)
