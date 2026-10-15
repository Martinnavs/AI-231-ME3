from __future__ import annotations

import asyncio
import textwrap
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from voice_agent.mcp_servers import code
from voice_agent.sandbox.runner import (
    HostLayout,
    SandboxPolicy,
    SandboxResult,
    build_command,
    preflight,
    run_in_sandbox,
)

LAYOUT = HostLayout(
    usr_merged={"/bin": True, "/lib": True, "/sbin": True},
    has_lib64=False,
    has_etc_alternatives=False,
)

Build = Callable[[Path, Path | None], Awaitable[list[str]]]


def _values(argv: list[str], flag: str, width: int) -> list[tuple[str, ...]]:
    return [tuple(argv[i + 1 : i + 1 + width]) for i, a in enumerate(argv) if a == flag]


@pytest.fixture
def build(monkeypatch: pytest.MonkeyPatch) -> Build:
    monkeypatch.setattr(code._state, "lock", asyncio.Lock())
    seen: list[SandboxPolicy] = []

    async def fake(policy: SandboxPolicy, argv: list[str]) -> SandboxResult:
        seen.append(policy)
        return SandboxResult(0, "", "", False, False, 0.1, 0, False)

    monkeypatch.setattr(code, "run_in_sandbox", fake)

    async def go(root: Path, media: Path | None) -> list[str]:
        monkeypatch.setattr(code._state, "root", root)
        monkeypatch.setattr(code._state, "media_root", media)
        seen.clear()
        await code.write_file("s.py", "")
        await code.run_python("s.py")
        assert len(seen) == 1
        return build_command(seen[0], ["python3", "/sandbox/s.py"], LAYOUT)

    return go


def _assert_masked_after_bind(argv: list[str], real_root: Path, hidden: str) -> None:
    assert _values(argv, "--bind", 2) == [(str(real_root), "/sandbox")]
    assert (f"/sandbox/{hidden}",) in _values(argv, "--tmpfs", 1)
    assert argv.index(f"/sandbox/{hidden}") > argv.index("--bind")


def _symlinked_home(tmp_path: Path) -> tuple[Path, Path]:
    real = tmp_path / "real"
    (real / "agent_sandbox").mkdir(parents=True)
    link = tmp_path / "home"
    link.symlink_to(real)
    return real, link


async def test_mask_with_symlinked_parent_of_sandbox_root(build: Build, tmp_path: Path) -> None:
    real, link = _symlinked_home(tmp_path)
    argv = await build(link / "agent_sandbox", link / "agent_sandbox" / "media")
    _assert_masked_after_bind(argv, (real / "agent_sandbox").resolve(), "media")


async def test_mask_media_via_real_path_root_via_symlink(build: Build, tmp_path: Path) -> None:
    real, link = _symlinked_home(tmp_path)
    argv = await build(link / "agent_sandbox", real / "agent_sandbox" / "media")
    _assert_masked_after_bind(argv, (real / "agent_sandbox").resolve(), "media")


async def test_mask_media_via_symlink_root_via_real_path(build: Build, tmp_path: Path) -> None:
    real, link = _symlinked_home(tmp_path)
    argv = await build(real / "agent_sandbox", link / "agent_sandbox" / "media")
    _assert_masked_after_bind(argv, (real / "agent_sandbox").resolve(), "media")


async def test_mask_nested_media_dir(build: Build, tmp_path: Path) -> None:
    root = tmp_path / "box"
    root.mkdir()
    argv = await build(root, root / "data" / "media")
    _assert_masked_after_bind(argv, root.resolve(), "data/media")


async def test_no_mask_when_media_outside_root(build: Build, tmp_path: Path) -> None:
    root = tmp_path / "box"
    root.mkdir()
    argv = await build(root, tmp_path / "media")
    assert _values(argv, "--tmpfs", 1) == [("/tmp",)]


async def test_symlinked_media_dir_refused(build: Build, tmp_path: Path) -> None:
    root = tmp_path / "box"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (root / "media").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="symlink"):
        await build(root, root / "media")


async def test_symlinked_media_dir_refused_under_symlinked_parent(
    build: Build, tmp_path: Path
) -> None:
    real, link = _symlinked_home(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (real / "agent_sandbox" / "media").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="symlink"):
        await build(link / "agent_sandbox", link / "agent_sandbox" / "media")


async def test_symlinked_intermediate_of_nested_media_refused(build: Build, tmp_path: Path) -> None:
    root = tmp_path / "box"
    root.mkdir()
    (tmp_path / "outside" / "media").mkdir(parents=True)
    (root / "data").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        await build(root, root / "data" / "media")


def test_hidden_path_escape_rejected() -> None:
    policy = SandboxPolicy(root=Path("/x"), hidden_paths=("../etc",))
    with pytest.raises(ValueError, match="relative"):
        build_command(policy, ["true"], LAYOUT)


_problems = preflight().problems


@pytest.mark.pi
@pytest.mark.skipif(bool(_problems), reason="sandbox not ready: " + "; ".join(_problems))
async def test_real_bwrap_hides_media(tmp_path: Path) -> None:
    root = tmp_path / "box"
    (root / "media").mkdir(parents=True)
    (root / "media" / "secret.wav").write_text("x")
    (root / "s.py").write_text(
        textwrap.dedent(
            """
            import os
            print(sorted(os.listdir("/sandbox/media")))
            open("/sandbox/media/new.wav", "w").write("1")
            """
        )
    )
    policy = SandboxPolicy(root=root, hidden_paths=("media",))
    res = await run_in_sandbox(policy, ["python3", "/sandbox/s.py"])
    assert res.stdout.strip() == "[]"
    assert not (root / "media" / "new.wav").exists()
