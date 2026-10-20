"""Integration tests for LAN speech server (P3, P4, P5, P6)."""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
from collections.abc import AsyncGenerator
from typing import Any

import pytest

websockets = pytest.importorskip("websockets")
from websockets.asyncio.client import connect  # noqa: E402

from lan_speech.engines import FakeSTTEngine, FakeTTSEngine, STTDecodeResult  # noqa: E402
from lan_speech.protocol import (  # noqa: E402
    CancelMessage,
    DoneMessage,
    ErrorMessage,
    FinalizeMessage,
    ReadyMessage,
    SpeakMessage,
    StartMessage,
    TranscriptMessage,
    unpack_tts_audio,
)
from lan_speech.server import LanSpeechServer  # noqa: E402


def ws_connect(uri: str, **kwargs: Any) -> Any:
    kwargs.setdefault("reconnect_delays", None)
    return connect(uri, **kwargs)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def test_server() -> AsyncGenerator[tuple[LanSpeechServer, int, str], None]:
    port = find_free_port()
    token = "secret-test-token-xyz"
    server = LanSpeechServer(
        stt_engine=FakeSTTEngine(),
        tts_engine=FakeTTSEngine(),
        host="127.0.0.1",
        port=port,
        token=token,
    )
    await server.start()
    try:
        yield server, port, token
    finally:
        await server.stop()


async def test_stt_stream_and_finalize(
    test_server: tuple[LanSpeechServer, int, str],
) -> None:
    """P3: fake engine; interim, final, one end_of_turn, two turns on one socket."""
    server, port, token = test_server
    uri = f"ws://127.0.0.1:{port}/stt"
    headers = {"Authorization": f"Bearer {token}"}

    async with ws_connect(uri, additional_headers=headers) as ws:
        # 1. Start message
        await ws.send(StartMessage(sample_rate=16000).to_json())

        # Turn 1:
        # Chunk 1 -> interim transcript
        dummy_pcm = b"\x00\x00" * 1600
        await ws.send(dummy_pcm)
        resp1 = json.loads(await ws.recv())
        tr1 = TranscriptMessage.from_json(resp1)
        assert tr1.text == "turn on"
        assert not tr1.is_final
        assert not tr1.end_of_turn

        # Chunk 2 -> final transcript with end_of_turn=True
        await ws.send(dummy_pcm)
        resp2 = json.loads(await ws.recv())
        tr2 = TranscriptMessage.from_json(resp2)
        assert tr2.text == "turn on the lights"
        assert tr2.is_final
        assert tr2.end_of_turn

        # Turn 2 on same socket without sending StartMessage again (Edge 4)
        await ws.send(dummy_pcm)
        resp3 = json.loads(await ws.recv())
        tr3 = TranscriptMessage.from_json(resp3)
        assert tr3.text == "turn on"
        assert not tr3.is_final

        # Finalize turn 2
        await ws.send(FinalizeMessage().to_json())
        resp4 = json.loads(await ws.recv())
        tr4 = TranscriptMessage.from_json(resp4)
        assert tr4.text == "final transcript"
        assert tr4.is_final
        assert tr4.end_of_turn


async def test_tts_cancel_no_stale_audio(
    test_server: tuple[LanSpeechServer, int, str],
) -> None:
    """P4: cancel after first chunk; no chunk of that context arrives afterwards; next clean."""
    server, port, token = test_server
    # Use fake engine with slight delay per chunk to guarantee cancel interleaving
    server.tts_engine = FakeTTSEngine(delay_s=0.05)
    uri = f"ws://127.0.0.1:{port}/tts"
    headers = {"Authorization": f"Bearer {token}"}

    async with ws_connect(uri, additional_headers=headers) as ws:
        # Expect ready message
        ready_raw = await ws.recv()
        ready = ReadyMessage.from_json(ready_raw)
        assert ready.sample_rate == server.tts_engine.sample_rate

        # Context 1: multiple clauses
        text1 = "Clause one; clause two; clause three; clause four."
        await ws.send(SpeakMessage(context=101, text=text1).to_json())

        # Receive chunk 1
        raw_audio1 = await ws.recv()
        assert isinstance(raw_audio1, bytes)
        ctx1, pcm1 = unpack_tts_audio(raw_audio1)
        assert ctx1 == 101

        # Cancel context 1 immediately
        await ws.send(CancelMessage(context=101).to_json())

        # Start context 2
        text2 = "Clean single clause."
        await ws.send(SpeakMessage(context=102, text=text2).to_json())

        # Receive message: must be for context 102! No stale 101 chunks or 101 done!
        msg = await ws.recv()
        if isinstance(msg, bytes):
            ctx, _ = unpack_tts_audio(msg)
            assert ctx == 102
        else:
            # If done arrived first (should not happen before audio)
            done = DoneMessage.from_json(msg)
            assert done.context == 102

        # Context 2 finishes with done
        done_raw = await ws.recv()
        done = DoneMessage.from_json(done_raw)
        assert done.context == 102


def http_get(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


async def test_auth_and_limits(
    test_server: tuple[LanSpeechServer, int, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P5: 401 without token, token never in logs (caplog), 413 and 503 paths."""
    server, port, token = test_server

    # 1. 401 on GET /voices without token
    status, body = await asyncio.to_thread(http_get, port, "/voices")
    assert status == 401
    assert body == b""

    # 2. 401 on WebSocket upgrade without token
    uri = f"ws://127.0.0.1:{port}/tts"
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with ws_connect(uri):
            pass

    # Assert secret token is NEVER in logs
    assert token not in caplog.text

    # 3. GET /healthz works without token
    status_hz, body_hz = await asyncio.to_thread(http_get, port, "/healthz")
    assert status_hz == 200
    hz_data = json.loads(body_hz)
    assert hz_data["ok"] is True

    # 4. GET /voices works WITH token
    status_v, body_v = await asyncio.to_thread(
        http_get, port, "/voices", {"Authorization": f"Bearer {token}"}
    )
    assert status_v == 200
    voices = json.loads(body_v)
    assert len(voices) > 0

    # 5. 413 path: frame larger than 64 KiB
    headers = {"Authorization": f"Bearer {token}"}
    async with ws_connect(uri, additional_headers=headers) as ws:
        await ws.recv()  # ready message
        # Send frame > 64 KiB
        large_frame = " " * (65 * 1024)
        await ws.send(large_frame)
        err_raw = await ws.recv()
        err = ErrorMessage.from_json(err_raw)
        assert err.status == 413
        assert "frame too large" in err.reason

    # 6. 413 path: speak text > 2000 chars
    async with ws_connect(uri, additional_headers=headers) as ws:
        await ws.recv()  # ready message
        long_text = "a" * 2001
        await ws.send(SpeakMessage(context=1, text=long_text).to_json())
        err_raw = await ws.recv()
        err = ErrorMessage.from_json(err_raw)
        assert err.status == 413
        assert "text too long" in err.reason

    # 7. 503 path: 5th connection rejected
    conns = []
    try:
        # Open 4 connections
        for _ in range(4):
            c = await ws_connect(uri, additional_headers=headers)
            await c.recv()  # ready
            conns.append(c)

        # 5th connection gets 503 error
        async with ws_connect(uri, additional_headers=headers) as c5:
            msg5 = await c5.recv()
            err5 = ErrorMessage.from_json(msg5)
            assert err5.status == 503
            assert err5.reason == "server busy"
    finally:
        for c in conns:
            await c.close()


async def test_disconnect_leaves_no_tasks(
    test_server: tuple[LanSpeechServer, int, str],
) -> None:
    """P6: client disconnects mid-synthesis; synthesis stops; no leaked task."""
    server, port, token = test_server
    server.tts_engine = FakeTTSEngine(delay_s=0.2)
    uri = f"ws://127.0.0.1:{port}/tts"
    headers = {"Authorization": f"Bearer {token}"}

    async with ws_connect(uri, additional_headers=headers) as ws:
        await ws.recv()  # ready
        # Start synthesizing 5 clauses (takes ~1.0s)
        text = "Clause one; clause two; clause three; clause four; clause five."
        await ws.send(SpeakMessage(context=501, text=text).to_json())
        # Receive chunk 1
        await ws.recv()
        # Abruptly close
        await ws.close()

    # Allow event loop to process disconnect and task cancellation
    await asyncio.sleep(0.1)

    # Server must have 0 active tasks
    assert len(server.active_tasks) == 0


async def test_stt_no_start_closes_socket(
    test_server: tuple[LanSpeechServer, int, str],
) -> None:
    """Edge 3: client sends audio before start -> 400 start required, socket closed."""
    server, port, token = test_server
    uri = f"ws://127.0.0.1:{port}/stt"
    headers = {"Authorization": f"Bearer {token}"}

    async with ws_connect(uri, additional_headers=headers) as ws:
        dummy_pcm = b"\x00\x00" * 1600
        await ws.send(dummy_pcm)
        resp = json.loads(await ws.recv())
        err = ErrorMessage.from_json(resp)
        assert err.status == 400
        assert err.reason == "start required"
        # Socket should be closed by server
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await ws.recv()


async def test_tts_two_contexts_busy(
    test_server: tuple[LanSpeechServer, int, str],
) -> None:
    """Edge 5: two speak contexts at once -> 409 context busy; first continues."""
    server, port, token = test_server
    server.tts_engine = FakeTTSEngine(delay_s=0.1)
    uri = f"ws://127.0.0.1:{port}/tts"
    headers = {"Authorization": f"Bearer {token}"}

    async with ws_connect(uri, additional_headers=headers) as ws:
        await ws.recv()  # ready
        # Send first speak
        await ws.send(SpeakMessage(context=1, text="First clause; second clause.").to_json())
        # Immediately send second speak
        await ws.send(SpeakMessage(context=2, text="Second speak.").to_json())

        # Expect 409 error for context 2
        msg = await ws.recv()
        if isinstance(msg, bytes):
            # Audio for context 1 arrived first, next message should be 409
            ctx, _ = unpack_tts_audio(msg)
            assert ctx == 1
            msg2 = await ws.recv()
            err = ErrorMessage.from_json(msg2)
            assert err.status == 409
            assert err.reason == "context busy"
        else:
            err = ErrorMessage.from_json(msg)
            assert err.status == 409
            assert err.reason == "context busy"


class ContextStream:
    """Engine stream that records the hints it is given and returns repair details."""

    def __init__(self, log: list[tuple[str | None, str | None]], answer: bool) -> None:
        self.log = log
        self.answer = answer
        self.chunks = 0

    def set_context(self, slot: str | None, risk: str | None) -> None:
        self.log.append((slot, risk))

    def accept_waveform(self, sample_rate: int, pcm_bytes: bytes) -> None:
        self.chunks += 1

    def decode(self) -> STTDecodeResult | None:
        if self.answer and self.chunks == 1:
            return STTDecodeResult(
                "call angeilo",
                True,
                True,
                raw_text="call angeilo",
                repair={"rung": "confirm", "question": "Did you mean Angelo?"},
            )
        return None

    def finalize(self) -> str:
        return "plain"

    def finalize_result(self) -> STTDecodeResult:
        return STTDecodeResult(
            "call Angelo", True, True, raw_text="call angeilo", repair={"rung": "silent"}
        )

    def reset(self) -> None:
        pass


async def test_stt_hints_reach_every_stream_and_repair_details_reach_the_client() -> None:
    log: list[tuple[str | None, str | None]] = []
    port = find_free_port()
    server = LanSpeechServer(
        stt_engine=FakeSTTEngine(stream_factory=lambda: ContextStream(log, answer=not log)),
        tts_engine=FakeTTSEngine(),
        host="127.0.0.1",
        port=port,
        insecure_loopback=True,
    )
    await server.start()
    try:
        async with ws_connect(f"ws://127.0.0.1:{port}/stt") as ws:
            await ws.send(StartMessage(slot="contact", risk="high").to_json())
            await ws.send(b"\x00\x00" * 1600)
            first = TranscriptMessage.from_json(json.loads(await ws.recv()))
            assert first.is_final and first.raw_text == "call angeilo"
            assert first.repair == {"rung": "confirm", "question": "Did you mean Angelo?"}

            # the turn ended, so the next audio opens a new stream: it must get the hints too
            await ws.send(b"\x00\x00" * 1600)
            await ws.send(FinalizeMessage().to_json())
            second = TranscriptMessage.from_json(json.loads(await ws.recv()))
            assert second.text == "call Angelo" and second.repair == {"rung": "silent"}
    finally:
        await server.stop()
    assert log == [("contact", "high"), ("contact", "high")]


async def test_stt_without_hints_or_details_keeps_the_old_wire_format(
    test_server: tuple[LanSpeechServer, int, str],
) -> None:
    _, port, token = test_server
    headers = {"Authorization": f"Bearer {token}"}
    async with ws_connect(f"ws://127.0.0.1:{port}/stt", additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "start", "sample_rate": 16000}))
        await ws.send(b"\x00\x00" * 1600)
        interim = json.loads(await ws.recv())
        assert set(interim) == {"type", "text", "is_final", "end_of_turn", "t_audio_end_s"}
