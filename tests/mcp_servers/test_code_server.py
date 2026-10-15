from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from voice_agent.mcp_servers import code
from voice_agent.sandbox.runner import SandboxPolicy, SandboxResult, SandboxUnavailable


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(code._state, "root", root)
    monkeypatch.setattr(code._state, "sites_root", tmp_path / "sites")
    monkeypatch.setattr(code._state, "sites_host", "pi.local")
    monkeypatch.setattr(code._state, "sites_port", 8081)
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
    await code.publish_site("dirlink", "ok")
    assert (sites / "ok/index.html").exists()


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
