from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from audio_engine import AudioClip
from audio_engine.wav import read_wav, write_wav

MODULE = "voice_agent.mcp_servers.speech"


def _args(media: Path) -> list[str]:
    return [
        "-m",
        MODULE,
        "--stt-backend",
        "fake",
        "--tts-backend",
        "fake",
        "--media-root",
        str(media),
    ]


@asynccontextmanager
async def _session(media: Path) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(command=sys.executable, args=_args(media))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def _text(result: CallToolResult) -> str:
    first = result.content[0]
    assert isinstance(first, TextContent)
    return first.text


@pytest.fixture
def media(tmp_path: Path) -> Path:
    m = tmp_path / "media"
    m.mkdir()
    return m


async def test_list_tools_and_resource(media: Path) -> None:
    async with _session(media) as s:
        tools = await s.list_tools()
        assert sorted(t.name for t in tools.tools) == [
            "get_tts_voices",
            "synthesize_speech_to_file",
            "transcribe_audio_file",
        ]
        resources = await s.list_resources()
        assert [str(r.uri) for r in resources.resources] == ["speech://voices"]
        res = await s.read_resource("speech://voices")  # type: ignore[arg-type]
        contents = res.contents[0]
        assert "fake" in getattr(contents, "text", "")
        voices = await s.call_tool("get_tts_voices", {})
        assert not voices.isError
        assert voices.structuredContent is not None


async def test_synthesize_then_transcribe_fake(media: Path) -> None:
    async with _session(media) as s:
        r = await s.call_tool(
            "synthesize_speech_to_file", {"text": "hello world", "path": "out/a.wav"}
        )
        assert not r.isError, _text(r)
        out = media / "out" / "a.wav"
        assert out.is_file()
        assert read_wav(out).duration_s == pytest.approx(11 * 0.060, abs=0.02)
        sc = r.structuredContent
        assert sc is not None
        assert sc["path"] == "out/a.wav"
        assert sc["sample_rate"] == 16000
        assert sc["duration_s"] == pytest.approx(0.66, abs=0.02)

        t = await s.call_tool("transcribe_audio_file", {"path": "out/a.wav"})
        assert not t.isError, _text(t)
        assert t.structuredContent is not None
        assert t.structuredContent["text"] == "hello"
        assert t.structuredContent["duration_s"] == pytest.approx(0.66, abs=0.02)


async def test_overwrite_and_missing_and_not_audio(media: Path) -> None:
    (media / "junk.wav").write_bytes(b"not audio at all")
    async with _session(media) as s:
        args = {"text": "hi", "path": "a.wav"}
        assert not (await s.call_tool("synthesize_speech_to_file", args)).isError
        again = await s.call_tool("synthesize_speech_to_file", args)
        assert again.isError and "exists" in _text(again)
        ok = await s.call_tool("synthesize_speech_to_file", {**args, "overwrite": True})
        assert not ok.isError
        missing = await s.call_tool("transcribe_audio_file", {"path": "nope.wav"})
        assert missing.isError and "nope.wav" in _text(missing)
        junk = await s.call_tool("transcribe_audio_file", {"path": "junk.wav"})
        assert junk.isError and "junk.wav" in _text(junk)


async def test_path_escape_is_tool_error(media: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.wav"
    write_wav(secret, AudioClip(np.zeros(1600, dtype=np.float32), 16000))
    (media / "link.wav").symlink_to(secret)
    async with _session(media) as s:
        for bad in ("/etc/passwd", "../secret.wav", "a/../../secret.wav", "link.wav"):
            r = await s.call_tool("transcribe_audio_file", {"path": bad})
            assert r.isError, bad
            assert "path outside media root" in _text(r)
        w = await s.call_tool("synthesize_speech_to_file", {"text": "x", "path": "../evil.wav"})
        assert w.isError and "path outside media root" in _text(w)
        w2 = await s.call_tool(
            "synthesize_speech_to_file", {"text": "x", "path": "link.wav", "overwrite": True}
        )
        assert w2.isError and "path outside media root" in _text(w2)
        assert not (tmp_path / "evil.wav").exists()
        assert read_wav(secret).duration_s == pytest.approx(0.1)
        ok = await s.call_tool("synthesize_speech_to_file", {"text": "x", "path": "fine.wav"})
        assert not ok.isError


async def test_cost_guards(media: Path) -> None:
    write_wav(media / "long.wav", AudioClip(np.zeros(16000 * 121, dtype=np.float32), 16000))
    write_wav(media / "edge.wav", AudioClip(np.zeros(16000 * 120, dtype=np.float32), 16000))
    async with _session(media) as s:
        r = await s.call_tool("synthesize_speech_to_file", {"text": "a" * 2001, "path": "big.wav"})
        assert r.isError and "too long" in _text(r)
        assert not (media / "big.wav").exists()
        ok = await s.call_tool("synthesize_speech_to_file", {"text": "a" * 2000, "path": "big.wav"})
        assert not ok.isError
        long = await s.call_tool("transcribe_audio_file", {"path": "long.wav"})
        assert long.isError and "too long" in _text(long)
        edge = await s.call_tool("transcribe_audio_file", {"path": "edge.wav"})
        assert not edge.isError


def test_stdout_only_jsonrpc(media: Path) -> None:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    msgs = [
        init,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "transcribe_audio_file", "arguments": {"path": "/etc/passwd"}},
        },
    ]
    proc = subprocess.Popen(
        [sys.executable, *_args(media)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    lines: list[str] = []
    try:
        for m in msgs:
            proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
            if "id" in m:
                lines.append(proc.stdout.readline())
    finally:
        proc.stdin.close()
        proc.stdin = None
        rest, stderr = proc.communicate(timeout=30)
    lines += rest.splitlines()
    lines = [ln for ln in lines if ln.strip()]
    assert len(lines) == 3
    for ln in lines:
        assert json.loads(ln)["jsonrpc"] == "2.0"
    assert "speech server" in stderr
