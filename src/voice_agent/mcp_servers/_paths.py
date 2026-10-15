from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class PathEscapeError(ValueError):
    pass


def resolve_inside(root: Path, rel: str, *, must_exist: bool) -> Path:
    if not rel or "\x00" in rel:
        raise PathEscapeError("path outside media root")
    if PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).anchor:
        raise PathEscapeError("path outside media root")
    if ".." in rel.replace("\\", "/").split("/"):
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
