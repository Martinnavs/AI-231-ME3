from __future__ import annotations

from pathlib import Path

import pytest

from voice_agent.mcp_servers._paths import PathEscapeError, resolve_inside


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "media"
    r.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.wav").write_bytes(b"x")
    (r / "link_dir").symlink_to(outside, target_is_directory=True)
    (r / "link_file.wav").symlink_to(outside / "secret.wav")
    (r / "a").mkdir()
    (r / "a" / "b.wav").write_bytes(b"x")
    (r / "a" / "inner_link.wav").symlink_to(r / "a" / "b.wav")
    return r


@pytest.mark.parametrize(
    "rel",
    [
        "/etc/passwd",
        "../x",
        "a/../../x",
        "a/..",
        "..",
        "a\\..\\..\\x",
        "",
        "bad\x00name",
        "link_dir/secret.wav",
        "link_dir/new.wav",
        "link_file.wav",
        "C:\\Windows\\x.wav",
    ],
)
@pytest.mark.parametrize("must_exist", [True, False])
def test_resolve_inside_rejects_escapes(root: Path, rel: str, must_exist: bool) -> None:
    with pytest.raises(PathEscapeError):
        resolve_inside(root, rel, must_exist=must_exist)


def test_absolute_path_inside_root_still_rejected(root: Path) -> None:
    with pytest.raises(PathEscapeError):
        resolve_inside(root, str(root / "a" / "b.wav"), must_exist=True)


def test_resolve_inside(root: Path) -> None:
    assert resolve_inside(root, "a/b.wav", must_exist=True) == (root / "a" / "b.wav").resolve()
    assert (
        resolve_inside(root, "a/inner_link.wav", must_exist=True)
        == (root / "a" / "b.wav").resolve()
    )
    assert resolve_inside(root, "new/dir/c.wav", must_exist=False) == (
        root.resolve() / "new" / "dir" / "c.wav"
    )
    assert resolve_inside(root, "a/./b.wav", must_exist=True) == (root / "a" / "b.wav").resolve()


def test_must_exist_missing(root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_inside(root, "a/nope.wav", must_exist=True)


def test_prefix_sibling_not_confused_with_root(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (tmp_path / "media2").mkdir()
    (root / "sib").symlink_to(tmp_path / "media2", target_is_directory=True)
    with pytest.raises(PathEscapeError):
        resolve_inside(root, "sib/x.wav", must_exist=False)
