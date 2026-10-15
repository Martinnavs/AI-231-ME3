from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
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


@pytest.fixture
def inproc(media: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from voice_agent.mcp_servers import speech

    monkeypatch.setattr(speech._state, "media_root", media)
    monkeypatch.setattr(speech._state, "transcriber", None)
    monkeypatch.setattr(speech._state, "synthesizer", None)
    monkeypatch.setattr(speech._state, "max_audio_s", 120.0)
    return media


async def test_dir_swapped_for_symlink_during_synthesis(
    inproc: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voice_agent.mcp_servers import speech

    outside = tmp_path / "outside"
    outside.mkdir()
    (inproc / "sub").mkdir()

    class Swapper:
        async def synthesize(self, text: str, voice: str | None = None) -> AudioClip:
            (inproc / "sub").rename(inproc / "sub_old")
            (inproc / "sub").symlink_to(outside)
            return AudioClip(np.zeros(1600, dtype=np.float32), 16000)

    monkeypatch.setattr(speech._state, "synthesizer", Swapper())
    with pytest.raises(ValueError):
        await speech.synthesize_speech_to_file("x", "sub/x.wav")
    assert list(outside.iterdir()) == []


async def test_high_rate_multichannel_flac_rejected_without_decoding(
    inproc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voice_agent.mcp_servers import speech

    sf.write(
        str(inproc / "bomb.wav"),
        np.zeros((655350, 8), dtype=np.float32),
        655350,
        format="FLAC",
    )
    assert (inproc / "bomb.wav").stat().st_size < 10_000
    calls: list[object] = []
    real = speech.read_wav
    monkeypatch.setattr(speech, "read_wav", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    with pytest.raises(ValueError, match="bomb.wav"):
        await speech.transcribe_audio_file("bomb.wav")
    assert calls == []


async def test_non_wav_format_and_oversize_caps(inproc: Path) -> None:
    from voice_agent.mcp_servers import speech

    sf.write(str(inproc / "ok_rate.wav"), np.zeros((100, 3), dtype=np.float32), 16000)
    with pytest.raises(ValueError, match="ok_rate.wav"):
        await speech.transcribe_audio_file("ok_rate.wav")
    sf.write(str(inproc / "hi.wav"), np.zeros(1000, dtype=np.float32), 96000)
    with pytest.raises(ValueError, match="hi.wav"):
        await speech.transcribe_audio_file("hi.wav")
    sf.write(str(inproc / "flac.wav"), np.zeros(1000, dtype=np.float32), 16000, format="FLAC")
    with pytest.raises(ValueError, match="flac.wav"):
        await speech.transcribe_audio_file("flac.wav")


async def test_hardlink_overwrite_does_not_touch_outside_file(inproc: Path, tmp_path: Path) -> None:
    from voice_agent.mcp_servers import speech

    outside = tmp_path / "outside.wav"
    write_wav(outside, AudioClip(np.zeros(1600, dtype=np.float32), 16000))
    before = outside.read_bytes()
    (inproc / "h.wav").hardlink_to(outside)
    await speech.synthesize_speech_to_file("hello", "h.wav", overwrite=True)
    assert outside.read_bytes() == before
    assert read_wav(inproc / "h.wav").duration_s == pytest.approx(5 * 0.060, abs=0.02)


async def test_write_error_names_only_relative_path(
    inproc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voice_agent.mcp_servers import speech

    def boom(*a: object, **k: object) -> None:
        raise OSError(f"cannot open {inproc}/secret-location")

    monkeypatch.setattr(sf, "write", boom)
    with pytest.raises(ValueError) as ei:
        await speech.synthesize_speech_to_file("hi", "o.wav")
    assert str(inproc) not in str(ei.value)
    assert "o.wav" in str(ei.value)


async def test_non_wav_output_rejected(inproc: Path) -> None:
    from voice_agent.mcp_servers import speech

    for bad in ("a.flac", "a.txt", "a", "a.wav.mp3"):
        with pytest.raises(ValueError, match=r"\.wav"):
            await speech.synthesize_speech_to_file("hi", bad)
    assert list(inproc.iterdir()) == []
    await speech.synthesize_speech_to_file("hi", "UP.WAV")
    assert (inproc / "UP.WAV").is_file()
    info = sf.info(str(inproc / "UP.WAV"))
    assert info.format == "WAV"


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "0", "-5"])
def test_max_audio_s_must_be_finite_positive(
    bad: str, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voice_agent.mcp_servers import speech

    monkeypatch.setattr(speech.mcp, "run", lambda **k: None)
    with pytest.raises(SystemExit) as ei:
        speech.main(["--media-root", str(media), "--max-audio-s", bad])
    assert ei.value.code == 2


FAKE_KEY = "sk-FAKEKEY-123456"
FAKE_URL = "wss://api.example.test/v1?api_key=" + FAKE_KEY


class _Boom:
    async def synthesize(self, text: str, voice: str | None = None) -> AudioClip:
        raise RuntimeError(f"connect failed {FAKE_URL} /home/user/secret")

    async def list_voices(self) -> list[object]:
        raise RuntimeError(f"connect failed {FAKE_URL}")

    async def transcribe(self, clip: AudioClip) -> object:
        raise RuntimeError(f"connect failed {FAKE_URL}")


async def test_backend_exception_text_not_forwarded(
    inproc: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from voice_agent.config import Secrets
    from voice_agent.mcp_servers import speech

    write_wav(inproc / "a.wav", AudioClip(np.zeros(1600, dtype=np.float32), 16000))
    monkeypatch.setattr(speech._state, "synthesizer", _Boom())
    monkeypatch.setattr(speech._state, "transcriber", _Boom())
    monkeypatch.setattr(
        speech,
        "load_config",
        lambda *a, **k: type("C", (), {"secrets": Secrets(cartesia_api_key=FAKE_KEY)})(),
    )
    calls = [
        speech.synthesize_speech_to_file("hi", "o.wav"),
        speech.get_tts_voices(),
        speech.voices_resource(),
        speech.transcribe_audio_file("a.wav"),
    ]
    for call in calls:
        with pytest.raises(ValueError) as ei:
            await call
        msg = str(ei.value)
        assert msg == "speech backend error: RuntimeError"
        assert FAKE_KEY not in msg and "example.test" not in msg
    assert FAKE_KEY not in caplog.text


async def test_backend_construction_failure_is_sanitised(
    inproc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voice_agent.mcp_servers import speech

    def bad(name: str, **kw: object) -> object:
        raise TypeError(f"missing {FAKE_URL}")

    monkeypatch.setattr(speech.audio_engine, "create_synthesizer", bad)
    with pytest.raises(ValueError) as ei:
        await speech.get_tts_voices()
    assert str(ei.value) == "speech backend error: TypeError"


async def test_speech_backend_error_reports_status_only(
    inproc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voice_agent.mcp_servers import speech

    class Err(speech.SpeechBackendError):  # type: ignore[name-defined,misc]
        status = 429

    class B:
        async def list_voices(self) -> list[object]:
            raise Err(f"body {FAKE_URL}")

    monkeypatch.setattr(speech._state, "synthesizer", B())
    with pytest.raises(ValueError) as ei:
        await speech.get_tts_voices()
    assert str(ei.value) == "speech backend error: Err (status 429)"


async def test_voice_length_capped(inproc: Path) -> None:
    from voice_agent.mcp_servers import speech

    with pytest.raises(ValueError, match="voice too long"):
        await speech.synthesize_speech_to_file("hi", "v.wav", voice="x" * 129)
    assert list(inproc.iterdir()) == []
    await speech.synthesize_speech_to_file("hi", "v.wav", voice="x" * 128)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores chmod")
async def test_permission_error_does_not_leak_host_path(inproc: Path) -> None:
    from voice_agent.mcp_servers import speech

    locked = inproc / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        for call in (
            lambda: speech.synthesize_speech_to_file("hi", "locked/o.wav"),
            lambda: speech.transcribe_audio_file("locked/a.wav"),
        ):
            with pytest.raises(ValueError) as ei:
                await call()
            assert str(inproc) not in str(ei.value)
            assert "Errno" not in str(ei.value)
    finally:
        locked.chmod(0o755)


async def test_redact_oserror_does_not_leak(
    inproc: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from voice_agent.mcp_servers import speech

    def boom(*a: object, **k: object) -> object:
        raise PermissionError(13, "denied", "/repo/.env")

    monkeypatch.setattr(speech, "load_config", boom)
    monkeypatch.setattr(speech._state, "synthesizer", _Boom())
    with pytest.raises(ValueError) as ei:
        await speech.get_tts_voices()
    assert str(ei.value) == "speech backend error: RuntimeError"
    assert "example.test" not in caplog.text and ".env" not in caplog.text


async def test_redaction_covers_encoded_prefix_and_query(
    inproc: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from voice_agent.config import Secrets
    from voice_agent.mcp_servers import speech

    key = "sk_FAKE+key/abc="

    class B:
        async def list_voices(self) -> list[object]:
            raise RuntimeError(
                "fail wss://api.example.test/tts?api_key=zzz%2Bq enc=sk_FAKE%2Bkey%2Fabc%3D "
                "plus=sk_FAKE+key%2Fabc%3D pre=sk_FAKE+key"
            )

    monkeypatch.setattr(speech._state, "synthesizer", B())
    monkeypatch.setattr(
        speech,
        "load_config",
        lambda *a, **k: type("C", (), {"secrets": Secrets(cartesia_api_key=key)})(),
    )
    with pytest.raises(ValueError):
        await speech.get_tts_voices()
    for leak in ("sk_FAKE", "zzz", "?api_key"):
        assert leak not in caplog.text
    assert "wss://api.example.test/tts" in caplog.text


@pytest.mark.parametrize("voice", ["a/b", "a\\b", "x\r\nX-Inj: 1", "v\x00", "a\x7fb", "a​b"])
async def test_voice_content_validated(inproc: Path, voice: str) -> None:
    from voice_agent.mcp_servers import speech

    with pytest.raises(ValueError, match="invalid voice"):
        await speech.synthesize_speech_to_file("hi", "v.wav", voice=voice)
    assert list(inproc.iterdir()) == []
