"""Stage marks (ticket 08): a scripted turn sends its stages to a UDP listener, in order."""

from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

pytest.importorskip("websockets")
pytest.importorskip("langchain_core")

from langchain_core.messages import HumanMessage  # noqa: E402

from doc_chat import agent_client, fdbench_marks  # noqa: E402
from doc_chat.__main__ import Session  # noqa: E402
from doc_chat.speech import DocChatSpeechClient  # noqa: E402
from lan_speech.protocol import DoneMessage, pack_tts_audio  # noqa: E402

SR = 16000


def block(level: float) -> bytes:
    t = np.arange(SR // 10) / SR
    return (level * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16).tobytes()


async def mic(*levels: float) -> Any:
    for lv in levels:
        yield block(lv)
        await asyncio.sleep(0)
    await asyncio.sleep(3600)


class SttWs:
    def __init__(self, text: str) -> None:
        self.text, self.inbox, self.sent = text, asyncio.Queue[str](), []

    async def send(self, data: Any) -> None:
        self.sent.append(data)
        if isinstance(data, str) and json.loads(data)["type"] == "finalize":
            await self.inbox.put(
                json.dumps(
                    {"type": "transcript", "text": self.text, "is_final": True, "end_of_turn": True}
                )
            )

    async def recv(self) -> str:
        return await self.inbox.get()


class TtsWs:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()

    async def send(self, data: str) -> None:
        ctx = json.loads(data)["context"]
        await self.queue.put(pack_tts_audio(ctx, b"\x01\x00" * 800))
        await self.queue.put(DoneMessage(context=ctx).to_json())

    async def recv(self) -> Any:
        return await self.queue.get()


class AgentWs:
    def __init__(self, *a: Any, **k: Any) -> None:
        self.replies = [
            {"event": "tool", "name": "weather", "said": False},
            {"text": "It is sunny."},
            {"done": True},
        ]

    def __enter__(self) -> AgentWs:
        return self

    def __exit__(self, *a: Any) -> None:
        pass

    def send(self, data: str) -> None:
        pass

    def recv(self, timeout: float = 0) -> str:
        return json.dumps(self.replies.pop(0))


@pytest.fixture
def listener(monkeypatch: pytest.MonkeyPatch) -> Any:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.5)
    monkeypatch.setenv("FDBENCH_MARKS", f"udp://127.0.0.1:{sock.getsockname()[1]}")
    monkeypatch.setattr(fdbench_marks, "_udp_checked", False)
    monkeypatch.setattr(fdbench_marks, "_udp", None)
    yield sock
    sock.close()
    monkeypatch.setattr(fdbench_marks, "_udp_checked", False)
    monkeypatch.setattr(fdbench_marks, "_udp", None)


def drain(sock: socket.socket) -> list[dict[str, Any]]:
    out = []
    try:
        while True:
            out.append(json.loads(sock.recv(65536)))
    except TimeoutError:
        return out


async def scripted_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DocChatSpeechClient()
    client._stt_ws = SttWs("what is the weather")  # type: ignore[assignment]
    client._tts_ws = TtsWs()  # type: ignore[assignment]
    heard = await asyncio.wait_for(
        client.listen_turn(
            audio=mic(0.0, 0.4, 0.4, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), vad_hold=0.3
        ),
        timeout=5,
    )
    assert heard.text == "what is the weather"
    monkeypatch.setattr(agent_client, "connect", lambda *a, **k: AgentWs())
    model = agent_client.AgentChatModel(url="ws://x", token="t")
    text = "".join(
        str(c.message.content) for c in model._stream([HumanMessage(content=heard.text)])
    )
    played: list[bytes] = []
    session = SimpleNamespace(
        args=SimpleNamespace(cue_beep=0, cue_every=0),
        client=SimpleNamespace(tts_sample_rate=24000),
        _replies=0,
        _cue_s=None,
        _current_is_say=False,
        _say_started=None,
        _filler_free_at=0.0,
        _turn_fillers=None,
        _answer_started=None,
    )
    push = Session._chunk(session, None)  # type: ignore[arg-type]
    await client.speak_text(
        text, t_speech_end=heard.t_speech_end, on_chunk=lambda p: (push(p), played.append(p))
    )
    assert played


async def test_a_scripted_turn_sends_its_stages_in_order(
    listener: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await scripted_turn(monkeypatch)
    marks = drain(listener)
    stages = [m["stage"] for m in marks]
    assert stages == [
        "vad_end",
        "asr_final",
        "llm_request",
        "tool_start",
        "llm_first_token",
        "tts_request",
        "tts_first_audio",
        "audio_out",
    ]
    by = {m["stage"]: m for m in marks}
    assert by["asr_final"]["text"] == "what is the weather"
    assert by["tool_start"]["name"] == "weather"
    assert by["llm_request"]["messages"] == 1
    times = [m["t"] for m in marks]
    assert times == sorted(times)


async def test_nothing_is_sent_when_the_variable_is_unset(
    listener: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FDBENCH_MARKS")
    monkeypatch.setattr(fdbench_marks, "_udp_checked", False)
    monkeypatch.setattr(fdbench_marks, "_udp", None)
    await scripted_turn(monkeypatch)
    assert drain(listener) == []
    assert not fdbench_marks.enabled()
