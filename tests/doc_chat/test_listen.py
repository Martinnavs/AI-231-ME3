"""listen_turn: the client's own pause detector, the server's end of turn, and the result."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest

pytest.importorskip("websockets")

from doc_chat.speech import DocChatSpeechClient  # noqa: E402

SR = 16000


def block(level: float) -> bytes:
    """100 ms of a 220 Hz tone at the given amplitude (RMS about 0.7 x level)."""
    t = np.arange(SR // 10) / SR
    return (level * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16).tobytes()


async def stream(*levels: float, pace: float = 0.0) -> AsyncIterator[bytes]:
    for lv in levels:
        yield block(lv)
        await asyncio.sleep(pace)
    await asyncio.sleep(3600)  # the microphone never ends by itself


class FakeSTTSocket:
    """Records what the client sends; answers `finalize` (or whatever the test queues)."""

    def __init__(self, on_finalize: dict[str, Any] | None = None) -> None:
        self.sent: list[Any] = []
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.on_finalize = on_finalize

    async def send(self, data: Any) -> None:
        self.sent.append(data)
        if isinstance(data, str) and json.loads(data)["type"] == "finalize" and self.on_finalize:
            await self.inbox.put(json.dumps({"type": "transcript", **self.on_finalize}))

    async def recv(self) -> str:
        return await self.inbox.get()

    def messages(self) -> list[dict[str, Any]]:
        return [json.loads(m) for m in self.sent if isinstance(m, str)]


def transcript(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "text": text,
        "is_final": True,
        "end_of_turn": True,
        "t_audio_end_s": None,
        **extra,
    }


def client(ws: FakeSTTSocket) -> DocChatSpeechClient:
    c = DocChatSpeechClient()
    c._stt_ws = ws
    return c


async def test_a_pause_makes_the_client_ask_the_server_to_end_the_turn() -> None:
    ws = FakeSTTSocket(on_finalize=transcript("who is dumbledore", raw_text="who is dumbledore"))
    heard = await asyncio.wait_for(
        client(ws).listen_turn(
            audio=stream(0.0, 0.0, 0.4, 0.4, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), vad_hold=0.5
        ),
        timeout=5,
    )
    assert heard.text == "who is dumbledore"
    assert heard.finalize_sent is not None and heard.server_ms is not None
    assert [m["type"] for m in ws.messages()] == ["start", "finalize"]


async def test_the_start_message_carries_the_slot_and_risk() -> None:
    ws = FakeSTTSocket(on_finalize=transcript("hi"))
    await asyncio.wait_for(
        client(ws).listen_turn(
            audio=stream(0.4, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            slot="doc?",
            risk="low",
            vad_hold=0.3,
        ),
        timeout=5,
    )
    assert ws.messages()[0] == {"type": "start", "sample_rate": SR, "slot": "doc?", "risk": "low"}


async def test_the_repair_summary_and_raw_text_reach_the_result() -> None:
    repair = {"rung": "confirm", "question": "Did you mean Dumbledore?", "matches": []}
    ws = FakeSTTSocket(
        on_finalize=transcript(
            "is dumbledor a wizard", raw_text="is dumbledor a wizard", repair=repair
        )
    )
    heard = await asyncio.wait_for(
        client(ws).listen_turn(audio=stream(0.4, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0), vad_hold=0.3),
        timeout=5,
    )
    assert heard.repair == repair and heard.raw_text == "is dumbledor a wizard"


async def test_when_the_server_ends_the_turn_first_no_finalize_is_needed() -> None:
    ws = FakeSTTSocket()
    await ws.inbox.put(json.dumps({"type": "transcript", **transcript("server decided")}))
    heard = await asyncio.wait_for(
        client(ws).listen_turn(audio=stream(0.4), vad_hold=5.0), timeout=5
    )
    assert heard.text == "server decided" and heard.finalize_sent is None
    assert heard.server_ms is None


async def test_with_a_hold_of_zero_the_client_never_sends_finalize() -> None:
    ws = FakeSTTSocket()
    await ws.inbox.put(json.dumps({"type": "transcript", **transcript("only the server")}))
    await asyncio.wait_for(
        client(ws).listen_turn(audio=stream(0.4, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), vad_hold=0),
        timeout=5,
    )
    assert "finalize" not in [m["type"] for m in ws.messages()]


async def test_an_empty_final_means_nothing_was_said_and_listening_goes_on() -> None:
    ws = FakeSTTSocket()
    await ws.inbox.put(json.dumps({"type": "transcript", **transcript("")}))
    await ws.inbox.put(json.dumps({"type": "transcript", **transcript("now I speak")}))
    heard = await asyncio.wait_for(
        client(ws).listen_turn(audio=stream(0.4), vad_hold=5.0), timeout=5
    )
    assert heard.text == "now I speak"


async def test_interim_text_is_passed_to_the_callback() -> None:
    ws = FakeSTTSocket()
    seen: list[str] = []
    await ws.inbox.put(
        json.dumps(
            {
                "type": "transcript",
                "text": "who i",
                "is_final": False,
                "end_of_turn": False,
                "t_audio_end_s": None,
            }
        )
    )
    await ws.inbox.put(json.dumps({"type": "transcript", **transcript("who is it")}))
    await asyncio.wait_for(
        client(ws).listen_turn(audio=stream(0.4), vad_hold=5.0, on_partial=seen.append), timeout=5
    )
    assert seen == ["who i"]


async def test_the_room_noise_carries_over_to_the_next_turn() -> None:
    ws = FakeSTTSocket(on_finalize=transcript("hello"))
    c = client(ws)
    assert c._noise.value is None
    quiet = [0.001] * 25
    await asyncio.wait_for(
        c.listen_turn(audio=stream(*quiet, 0.4, 0.4, *quiet[:8]), vad_hold=0.3), timeout=5
    )
    assert c._noise.value is not None and c._noise.value < 0.01
