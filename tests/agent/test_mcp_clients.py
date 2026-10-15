from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_agent.agent.mcp_clients import McpClientManager, _expand_env, _render
from voice_agent.config import McpServerSpec


def _py(module: str, *args: str, env: dict[str, str] | None = None) -> McpServerSpec:
    return McpServerSpec(
        command=sys.executable,
        args=("-m", f"voice_agent.mcp_servers.{module}", *args),
        env=env or {},
    )


@pytest.fixture
def specs(tmp_path: Path) -> dict[str, McpServerSpec]:
    media = tmp_path / "media"
    media.mkdir()
    return {
        "speech": _py(
            "speech",
            "--stt-backend",
            "fake",
            "--tts-backend",
            "fake",
            "--media-root",
            str(media),
        ),
        "home": _py(
            "home", "--base-url", "http://127.0.0.1:9", "--state-dir", str(tmp_path / "st")
        ),
        "code": _py("code", "--root", str(tmp_path / "box")),
    }


def _children() -> set[int]:
    me = os.getpid()
    found: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        if int(stat.rsplit(")", 1)[1].split()[1]) == me:
            found.add(int(entry.name))
    return found


async def test_loads_three_real_servers(specs: dict[str, McpServerSpec]) -> None:
    async with McpClientManager(specs, start_timeout_s=60) as reg:
        names = [t.name for t in reg.tools]
        assert names == sorted(names)
        assert reg.unavailable == {}
        assert "speech__transcribe_audio_file" in names
        assert "speech__synthesize_speech_to_file" in names
        assert "home__control_lights" in names
        assert {"code__write_file", "code__run_python", "code__publish_site"} <= set(names)
        tool = next(t for t in reg.tools if t.name == "speech__get_tts_voices")
        assert (tool.server, tool.tool) == ("speech", "get_tts_voices")
        assert tool.input_schema["type"] == "object"


async def test_one_broken_server_does_not_block_others(specs: dict[str, McpServerSpec]) -> None:
    specs["broken"] = McpServerSpec(command="false")
    async with McpClientManager(specs, start_timeout_s=60) as reg:
        assert "broken" in reg.unavailable and reg.unavailable["broken"]
        assert any(t.name.startswith("speech__") for t in reg.tools)
        assert not any(t.name.startswith("broken__") for t in reg.tools)
        out = await reg.call("speech__get_tts_voices", {})
        assert not out.is_error


async def test_missing_command_and_start_timeout() -> None:
    sleeper = McpServerSpec(command=sys.executable, args=("-c", "import time; time.sleep(60)"))
    async with McpClientManager(
        {"nope": McpServerSpec(command="/nonexistent/x"), "slow": sleeper},
        start_timeout_s=1.5,
    ) as reg:
        assert set(reg.unavailable) == {"nope", "slow"}
        assert reg.unavailable["slow"] == "timed out starting"
        assert reg.tools == []


async def test_env_expansion_and_unset_var(
    specs: dict[str, McpServerSpec], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECAP_TEST_VAR", "v1")
    monkeypatch.delenv("RECAP_MISSING_VAR", raising=False)
    assert _expand_env({"A": "x-${RECAP_TEST_VAR}"}) == {"A": "x-v1"}
    good = McpServerSpec(
        command=specs["speech"].command,
        args=specs["speech"].args,
        env={"X": "${RECAP_TEST_VAR}"},
    )
    bad = McpServerSpec(
        command=specs["speech"].command,
        args=specs["speech"].args,
        env={"X": "${RECAP_MISSING_VAR}"},
    )
    async with McpClientManager({"good": good, "bad": bad}, start_timeout_s=60) as reg:
        assert any(t.name.startswith("good__") for t in reg.tools)
        assert not any(t.name.startswith("bad__") for t in reg.tools)
        assert "RECAP_MISSING_VAR" in reg.unavailable["bad"]


async def test_call_roundtrip_speech_fake(specs: dict[str, McpServerSpec]) -> None:
    async with McpClientManager({"speech": specs["speech"]}, start_timeout_s=60) as reg:
        syn = await reg.call(
            "speech__synthesize_speech_to_file", {"text": "hello world", "path": "a/b.wav"}
        )
        assert not syn.is_error, syn.text
        assert "b.wav" in syn.text and syn.duration_s > 0
        stt = await reg.call("speech__transcribe_audio_file", {"path": "a/b.wav"})
        assert not stt.is_error, stt.text
        assert "text" in stt.text
        escape = await reg.call("speech__transcribe_audio_file", {"path": "../x.wav"})
        assert escape.is_error and "outside media root" in escape.text
        unknown = await reg.call("speech__nope", {})
        assert unknown.is_error


async def test_call_timeout(specs: dict[str, McpServerSpec]) -> None:
    async with McpClientManager({"speech": specs["speech"]}, start_timeout_s=60) as reg:
        out = await reg.call("speech__get_tts_voices", {}, timeout_s=0.0)
        assert out == type(out)("timed out", True, out.duration_s)
        assert not (await reg.call("speech__get_tts_voices", {})).is_error


async def test_server_killed_mid_session_restarts_once(specs: dict[str, McpServerSpec]) -> None:
    async with McpClientManager({"speech": specs["speech"]}, start_timeout_s=60) as reg:
        pids = _children()
        assert len(pids) == 1
        (old,) = pids
        os.kill(old, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            time.sleep(0.1)
            try:
                os.kill(old, 0)
            except ProcessLookupError:
                break
        out = await reg.call("speech__get_tts_voices", {})
        assert not out.is_error, out.text
        new = _children()
        assert len(new) == 1 and old not in new


async def test_dead_server_that_cannot_restart_reports_unavailable(
    tmp_path: Path,
) -> None:
    flag = tmp_path / "once"
    script = (
        "import sys, pathlib\n"
        f"f = pathlib.Path({str(flag)!r})\n"
        "if f.exists(): sys.exit(1)\n"
        "f.write_text('x')\n"
        "from voice_agent.mcp_servers import speech\n"
        "sys.exit(speech.main(['--stt-backend','fake','--tts-backend','fake',"
        f"'--media-root',{str(tmp_path)!r}]))\n"
    )
    spec = McpServerSpec(command=sys.executable, args=("-c", script))
    async with McpClientManager({"speech": spec}, start_timeout_s=60) as reg:
        (pid,) = _children()
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.3)
        out = await reg.call("speech__get_tts_voices", {})
        assert out.is_error and out.text == "server speech unavailable"


async def test_subset_and_anthropic_tools_shape(specs: dict[str, McpServerSpec]) -> None:
    async with McpClientManager(specs, start_timeout_s=60) as reg:
        sub = reg.subset(["speech__"], exclude=["speech__get_tts_voices"])
        names = [t.name for t in sub.tools]
        assert names and all(n.startswith("speech__") for n in names)
        assert "speech__get_tts_voices" not in names
        assert not any(n.startswith("code__") for n in names)

        both = reg.subset(["code__", "speech__"])
        assert {t.server for t in both.tools} == {"code", "speech"}

        shaped = sub.anthropic_tools()
        assert [d["name"] for d in shaped] == names
        for d, t in zip(shaped, sub.tools, strict=True):
            assert set(d) == {"name", "description", "input_schema"}
            assert d["input_schema"] == t.input_schema

        assert (await reg.call("speech__get_tts_voices", {})).is_error is False
        assert (await sub.call("speech__get_tts_voices", {})).is_error is True


async def test_no_zombies_after_close(specs: dict[str, McpServerSpec]) -> None:
    assert _children() == set()
    async with McpClientManager(specs, start_timeout_s=60) as reg:
        assert len(_children()) == 3
        assert reg.tools
    assert _children() == set()


def test_render_joins_text_and_summarizes_other_blocks() -> None:
    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="a"),
            SimpleNamespace(type="image"),
            SimpleNamespace(type="text", text="b"),
        ]
    )
    assert _render(result) == "a\n[image omitted]\nb"
