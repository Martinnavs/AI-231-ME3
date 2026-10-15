from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from voice_agent.mcp_servers import _paths, code
from voice_agent.sandbox.runner import SandboxPolicy, SandboxResult, SandboxUnavailable


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(code._state, "root", root)
    monkeypatch.setattr(code._state, "sites_root", tmp_path / "sites")
    monkeypatch.setattr(code._state, "sites_host", "pi.local")
    monkeypatch.setattr(code._state, "sites_port", 8081)
    monkeypatch.setattr(code._state, "media_root", root / "media", raising=False)
    monkeypatch.setattr(code._state, "lock", asyncio.Lock())
    yield root


async def test_code_server_paths_and_size_limits(box: Path, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("s")
    (box / "link").symlink_to(tmp_path / "secret.txt")
    for bad in ("../secret.txt", "/etc/passwd", "link", "a/../../x", ""):
        with pytest.raises(ValueError, match="outside sandbox root"):
            await code.write_file(bad, "x")
        with pytest.raises(ValueError, match="outside sandbox root"):
            await code.read_file(bad)
    with pytest.raises(ValueError, match="outside sandbox root"):
        await code.list_files("..")
    assert (tmp_path / "secret.txt").read_text() == "s"

    with pytest.raises(ValueError, match="too large"):
        await code.write_file("big.txt", "a" * (257 * 1024))
    assert not (box / "big.txt").exists()
    await code.write_file("exact.txt", "a" * (256 * 1024))

    assert await code.write_file("pkg/hello.py", "print(1)") == {"path": "pkg/hello.py", "bytes": 8}
    assert await code.read_file("pkg/hello.py") == "print(1)"
    assert "pkg/" in await code.list_files(".")
    assert await code.list_files("pkg") == ["hello.py"]
    with pytest.raises(ValueError, match="not found"):
        await code.read_file("nope.txt")
    with pytest.raises(ValueError, match="directory"):
        await code.write_file("pkg", "x")


async def test_run_python_unavailable(box: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await code.write_file("s.py", "print(1)")

    async def boom(policy: SandboxPolicy, argv: object) -> SandboxResult:
        raise SandboxUnavailable("sandbox unavailable: install bubblewrap")

    monkeypatch.setattr(code, "run_in_sandbox", boom)
    with pytest.raises(ValueError, match="sandbox unavailable: install bubblewrap"):
        await code.run_python("s.py")
    with pytest.raises(ValueError, match="outside sandbox root"):
        await code.run_python("../x.py")


async def test_run_python_unpatched_without_bwrap(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("voice_agent.sandbox.runner.shutil.which", lambda *a, **k: None)
    await code.write_file("s.py", "print(1)")
    with pytest.raises(ValueError, match="sandbox unavailable: install bubblewrap"):
        await code.run_python("s.py")


async def test_run_python_policy_and_result(box: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake(policy: SandboxPolicy, argv: list[str]) -> SandboxResult:
        seen["policy"], seen["argv"] = policy, argv
        return SandboxResult(0, "out", "", False, False, 0.5)

    monkeypatch.setattr(code, "run_in_sandbox", fake)
    await code.write_file("d/s.py", "")
    res = await code.run_python("d/s.py", ["a"], timeout_s=9999)
    assert res["stdout"] == "out" and res["returncode"] == 0
    policy = seen["policy"]
    assert isinstance(policy, SandboxPolicy)
    assert policy.timeout_s == 120 and not policy.network and policy.root == box.resolve()
    assert seen["argv"] == ["python3", "/sandbox/d/s.py", "a"]


def _site(box: Path) -> Path:
    site = box / "web"
    (site / "css").mkdir(parents=True)
    (site / "index.html").write_text("<h1>old</h1>")
    (site / "css/a.css").write_text("body{}")
    return site


async def test_publish_site(box: Path, tmp_path: Path) -> None:
    _site(box)
    res = await code.publish_site("web", "demo")
    assert res == {"url": "http://pi.local:8081/demo/", "files": 2, "bytes": 18}
    sites = tmp_path / "sites"
    assert (sites / "demo/index.html").read_text() == "<h1>old</h1>"
    assert (sites / "demo/css/a.css").exists()
    assert oct(sites.stat().st_mode & 0o777) == "0o755"
    assert oct((sites / "demo/index.html").stat().st_mode & 0o777) == "0o644"

    (box / "web/index.html").write_text("<h1>new</h1>")
    (box / "web/css/a.css").unlink()
    await code.publish_site("web", "demo")
    assert (sites / "demo/index.html").read_text() == "<h1>new</h1>"
    assert not (sites / "demo/css/a.css").exists()
    assert [p.name for p in sites.iterdir()] == ["demo"]


async def test_publish_site_rejects_bad_entries(box: Path, tmp_path: Path) -> None:
    site = _site(box)
    sites = tmp_path / "sites"
    sites.mkdir()
    (tmp_path / "outside.txt").write_text("x")
    (site / "css/evil").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ValueError, match=r"symlink: css/evil"):
        await code.publish_site("web", "demo")
    assert list(sites.iterdir()) == []
    (site / "css/evil").unlink()

    os.mkfifo(site / "pipe")
    with pytest.raises(ValueError, match="special file: pipe"):
        await code.publish_site("web", "demo")
    assert list(sites.iterdir()) == []
    (site / "pipe").unlink()

    (box / "dirlink").symlink_to(site)
    with pytest.raises(ValueError, match="outside sandbox root|symlink"):
        await code.publish_site("../web", "demo")
    with pytest.raises(ValueError, match="not a directory|outside sandbox root"):
        await code.publish_site("dirlink", "ok")
    assert list(sites.iterdir()) == []


async def test_publish_site_failed_republish_keeps_old(box: Path, tmp_path: Path) -> None:
    site = _site(box)
    await code.publish_site("web", "demo")
    (site / "bad").symlink_to("index.html")
    with pytest.raises(ValueError, match="symlink"):
        await code.publish_site("web", "demo")
    assert (tmp_path / "sites/demo/index.html").read_text() == "<h1>old</h1>"


async def test_publish_site_caps(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = box / "many"
    site.mkdir()
    for i in range(201):
        (site / f"{i}.txt").write_text("x")
    with pytest.raises(ValueError, match="201 files"):
        await code.publish_site("many", "demo")
    assert not (tmp_path / "sites").exists()

    monkeypatch.setattr(code, "MAX_SITE_BYTES", 10)
    big = box / "big"
    big.mkdir()
    (big / "a.bin").write_bytes(b"x" * 11)
    with pytest.raises(ValueError, match="11 bytes"):
        await code.publish_site("big", "demo")
    assert not (tmp_path / "sites").exists()


@pytest.mark.parametrize("name", ["../x", "Demo", "a" * 41, "", "-a", "a/b", "a.b", "x\n"])
async def test_publish_site_bad_names(box: Path, tmp_path: Path, name: str) -> None:
    _site(box)
    with pytest.raises(ValueError, match="invalid site name"):
        await code.publish_site("web", name)
    assert not (tmp_path / "sites").exists()


async def test_publish_site_name_limit_and_missing_source(box: Path) -> None:
    _site(box)
    await code.publish_site("web", "a" * 40)
    with pytest.raises(ValueError, match="not found"):
        await code.publish_site("nope", "demo")


def _swap_after_resolve(monkeypatch: pytest.MonkeyPatch, swap: Callable[[], None]) -> None:
    real = _paths.resolve_inside
    done: list[bool] = []

    def spy(root: Path, rel: str, *, must_exist: bool) -> Path:
        out = real(root, rel, must_exist=must_exist)
        if not done:
            done.append(True)
            swap()
        return out

    monkeypatch.setattr(_paths, "resolve_inside", spy)
    monkeypatch.setattr(code, "resolve_inside", spy, raising=False)


async def test_write_file_parent_swap_race(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (box / "sub").mkdir()

    def swap() -> None:
        shutil.rmtree(box / "sub")
        (box / "sub").symlink_to(outside)

    _swap_after_resolve(monkeypatch, swap)
    with pytest.raises(ValueError, match="outside sandbox root"):
        await code.write_file("sub/x.txt", "pwned")
    assert list(outside.iterdir()) == []


async def test_read_file_symlink_swap_race(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "key").write_text("TOPSECRET")
    (box / "a.txt").write_text("fine")

    def swap() -> None:
        (box / "a.txt").unlink()
        (box / "a.txt").symlink_to(tmp_path / "key")

    _swap_after_resolve(monkeypatch, swap)
    with pytest.raises(ValueError, match="outside sandbox root"):
        await code.read_file("a.txt")


async def test_read_file_fifo_swap_does_not_hang(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (box / "a.txt").write_text("fine")

    def swap() -> None:
        (box / "a.txt").unlink()
        os.mkfifo(box / "a.txt")

    _swap_after_resolve(monkeypatch, swap)
    with pytest.raises(ValueError, match="not a file"):
        await asyncio.wait_for(code.read_file("a.txt"), 3)


async def test_root_symlink_refused(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "rootlink"
    link.symlink_to(box)
    monkeypatch.setattr(code._state, "root", link)
    with pytest.raises(ValueError, match="outside sandbox root"):
        await code.write_file("x.txt", "x")
    with pytest.raises(ValueError, match="outside sandbox root"):
        await code.list_files(".")
    assert not (box / "x.txt").exists()


async def test_tools_serialize_on_lock(box: Path) -> None:
    await code.write_file("a.txt", "1")
    await code._state.lock.acquire()
    tasks = [
        asyncio.ensure_future(code.write_file("b.txt", "2")),
        asyncio.ensure_future(code.read_file("a.txt")),
        asyncio.ensure_future(code.list_files(".")),
    ]
    await asyncio.sleep(0.1)
    assert not any(t.done() for t in tasks)
    code._state.lock.release()
    await asyncio.gather(*tasks)


def _spy_scandir(monkeypatch: pytest.MonkeyPatch, swap: Callable[[], None]) -> None:
    real = os.scandir
    fired: list[bool] = []

    class Spy:
        def __init__(self, *a: Any, **k: Any) -> None:
            self.it = real(*a, **k)

        def __enter__(self) -> Any:
            return self.it.__enter__()

        def __exit__(self, *exc: Any) -> Any:
            res = self.it.__exit__(*exc)
            if not fired:
                fired.append(True)
                swap()
            return res

    monkeypatch.setattr(os, "scandir", Spy)


async def test_publish_scan_copy_race(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _site(box)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    secret.chmod(0o600)

    def swap() -> None:
        (site / "index.html").unlink()
        (site / "index.html").symlink_to(secret)

    _spy_scandir(monkeypatch, swap)
    with contextlib.suppress(ValueError):
        await code.publish_site("web", "demo")
    assert oct(secret.stat().st_mode & 0o777) == "0o600"
    for p in (tmp_path / "sites").rglob("*"):
        assert not p.is_symlink()
        assert not (p.is_file() and p.read_text() == "TOPSECRET")


async def test_publish_dir_swap_race(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _site(box)
    outdir = tmp_path / "outdir"
    outdir.mkdir()
    (outdir / "loot.txt").write_text("TOPSECRET")

    def swap() -> None:
        shutil.rmtree(site / "css")
        (site / "css").symlink_to(outdir)

    _spy_scandir(monkeypatch, swap)
    with contextlib.suppress(ValueError):
        await code.publish_site("web", "demo")
    assert not list((tmp_path / "sites").rglob("loot.txt"))


async def test_publish_rejects_root_and_media(box: Path, tmp_path: Path) -> None:
    (box / "index.html").write_text("x")
    for sub in (".", "./"):
        with pytest.raises(ValueError):
            await code.publish_site(sub, "demo")
    (box / "media/deep").mkdir(parents=True)
    (box / "media/a.wav").write_text("x")
    with pytest.raises(ValueError, match="media"):
        await code.publish_site("media", "demo")
    with pytest.raises(ValueError, match="media"):
        await code.publish_site("media/deep", "demo")
    assert not (tmp_path / "sites").exists()


async def test_publish_skips_dot_entries(box: Path, tmp_path: Path) -> None:
    site = _site(box)
    (site / ".env").write_text("KEY=1")
    (site / ".git").mkdir()
    (site / ".git/config").write_text("x")
    res = await code.publish_site("web", "demo")
    assert res["files"] == 2
    assert sorted(p.name for p in (tmp_path / "sites/demo").iterdir()) == ["css", "index.html"]


async def test_publish_dir_count_and_depth_caps(box: Path, tmp_path: Path) -> None:
    path = box / "deep"
    for i in range(code.MAX_SITE_DEPTH + 2):
        path = path / f"d{i}"
    path.mkdir(parents=True)
    with pytest.raises(ValueError, match="deep"):
        await code.publish_site("deep", "demo")

    wide = box / "wide"
    wide.mkdir()
    for i in range(code.MAX_SITE_DIRS + 1):
        (wide / f"d{i}").mkdir()
    with pytest.raises(ValueError, match="directories"):
        await code.publish_site("wide", "demo")
    assert not (tmp_path / "sites").exists()


async def test_publish_staging_private_until_complete(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _site(box)
    created: list[int] = []
    real_mkdtemp = code.tempfile.mkdtemp

    def mk(*a: Any, **k: Any) -> str:
        out = real_mkdtemp(*a, **k)
        created.append(os.stat(out).st_mode & 0o777)
        return out

    monkeypatch.setattr(code.tempfile, "mkdtemp", mk)
    seen: list[int] = []
    real = pathlib.Path.rename

    def spy(self: Path, target: Any) -> Path:
        if self.name.startswith(".tmp-"):
            seen.append(self.stat().st_mode & 0o777)
        return real(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", spy)
    await code.publish_site("web", "demo")
    assert created and all(m & 0o077 == 0 for m in created)
    assert seen == [0o755]
    assert [p.name for p in (tmp_path / "sites").iterdir()] == ["demo"]


async def test_failed_republish_restores_old_site(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _site(box)
    await code.publish_site("web", "demo")
    (box / "web/index.html").write_text("<h1>new</h1>")
    real = pathlib.Path.rename

    def flaky(self: Path, target: Any) -> Path:
        if self.name.startswith(".tmp-"):
            raise OSError("disk exploded")
        return real(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", flaky)
    with pytest.raises(OSError, match="exploded"):
        await code.publish_site("web", "demo")
    sites = tmp_path / "sites"
    assert (sites / "demo/index.html").read_text() == "<h1>old</h1>"
    assert [p.name for p in sites.iterdir()] == ["demo"]


async def test_run_python_masks_media_and_checks_size(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def fake(policy: SandboxPolicy, argv: list[str]) -> SandboxResult:
        seen["policy"] = policy
        return SandboxResult(0, "", "", False, False, 0.1, 0, False)

    monkeypatch.setattr(code, "run_in_sandbox", fake)
    await code.write_file("s.py", "")
    await code.run_python("s.py")
    policy = seen["policy"]
    assert isinstance(policy, SandboxPolicy) and policy.hidden_paths == ("media",)

    (box / "media").symlink_to("/tmp")
    with pytest.raises(ValueError, match="media"):
        await code.run_python("s.py")
    (box / "media").unlink()

    async def big(policy: SandboxPolicy, argv: list[str]) -> SandboxResult:
        return SandboxResult(0, "", "", False, False, 0.1, 10**9, True)

    monkeypatch.setattr(code, "run_in_sandbox", big)
    with pytest.raises(ValueError, match="over the limit"):
        await code.run_python("s.py")


async def _capture_policy(monkeypatch: pytest.MonkeyPatch) -> dict[str, SandboxPolicy]:
    seen: dict[str, SandboxPolicy] = {}

    async def fake(policy: SandboxPolicy, argv: list[str]) -> SandboxResult:
        seen["policy"] = policy
        return SandboxResult(0, "", "", False, False, 0.1, 0, False)

    monkeypatch.setattr(code, "run_in_sandbox", fake)
    return seen


async def test_media_mask_survives_symlinked_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    (real / "agent_sandbox").mkdir(parents=True)
    link = tmp_path / "home"
    link.symlink_to(real)
    root = link / "agent_sandbox"
    monkeypatch.setattr(code._state, "root", root)
    monkeypatch.setattr(code._state, "media_root", root / "media")
    monkeypatch.setattr(code._state, "lock", asyncio.Lock())
    seen = await _capture_policy(monkeypatch)
    await code.write_file("s.py", "")
    await code.run_python("s.py")
    assert seen["policy"].hidden_paths == ("media",)

    monkeypatch.setattr(code._state, "media_root", real / "agent_sandbox" / "media")
    assert code._media_rel() == "media"
    monkeypatch.setattr(code._state, "root", real / "agent_sandbox")
    monkeypatch.setattr(code._state, "media_root", root / "media")
    assert code._media_rel() == "media"


async def test_media_symlinked_intermediate_component_refused(
    box: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(code._state, "media_root", box / "a" / "media")
    outside = tmp_path / "outside"
    (outside / "media").mkdir(parents=True)
    (box / "a").symlink_to(outside)
    await _capture_policy(monkeypatch)
    await code.write_file("s.py", "")
    with pytest.raises(ValueError, match="symlink"):
        await code.run_python("s.py")


async def test_list_files_caps_entries(box: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code, "MAX_LIST_ENTRIES", 5)
    for i in range(8):
        (box / f"f{i}").write_text("")
    out = await code.list_files(".")
    assert len(out) == 6 and out[-1].startswith("…[truncated")
    assert str(box) not in "".join(out)
