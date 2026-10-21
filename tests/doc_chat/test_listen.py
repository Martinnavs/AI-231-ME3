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


async def test_the_result_carries_the_audio_up_to_the_finalize() -> None:
    ws = FakeSTTSocket(on_finalize=transcript("who is dumbledore"))
    heard = await asyncio.wait_for(
        client(ws).listen_turn(
            audio=stream(0.0, 0.0, 0.4, 0.4, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            vad_hold=0.5,
        ),
        timeout=5,
    )
    sent = [m for m in ws.sent if isinstance(m, bytes)]
    assert heard.pcm and len(heard.pcm) % (SR // 10 * 2) == 0
    # the audio recorded after the finalize (while the server worked) is not part of this turn
    assert len(heard.pcm) <= sum(len(b) for b in sent)
    assert heard.pcm == b"".join(sent)[: len(heard.pcm)]


async def test_nothing_said_within_the_wait_raises_no_speech() -> None:
    from doc_chat.speech import NoSpeech

    ws = FakeSTTSocket()
    with pytest.raises(NoSpeech):
        await asyncio.wait_for(
            client(ws).listen_turn(audio=stream(0.0, 0.0, 0.0), first_speech_timeout=0.4),
            timeout=5,
        )


async def test_speech_inside_the_wait_is_not_cut_off() -> None:
    ws = FakeSTTSocket(on_finalize=transcript("by taylor swift"))
    heard = await asyncio.wait_for(
        client(ws).listen_turn(
            audio=stream(0.0, 0.4, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            vad_hold=0.4,
            first_speech_timeout=1.5,
        ),
        timeout=5,
    )
    assert heard.text == "by taylor swift"


async def test_hear_pcm_sends_the_audio_unpaced_then_asks_for_the_end() -> None:
    ws = FakeSTTSocket(on_finalize=transcript("play something by taylor swift"))
    pcm = block(0.4) * 12
    heard = await asyncio.wait_for(
        client(ws).hear_pcm(pcm, slot="doc?", t_speech_end=123.0), timeout=5
    )
    assert [m["type"] for m in ws.messages()] == ["start", "finalize"]
    assert b"".join(m for m in ws.sent if isinstance(m, bytes)) == pcm
    assert heard.text == "play something by taylor swift"
    assert heard.t_speech_end == 123.0 and heard.pcm == pcm


async def test_no_audio_is_sent_after_finalize_until_the_server_answers() -> None:
    """Audio sent then would start a stream on the server whose late end-of-turn comes back as a
    stale transcript in the next turn."""
    ws = FakeSTTSocket()  # never answers the finalize
    task = asyncio.create_task(
        client(ws).listen_turn(
            audio=stream(0.0, 0.4, 0.4, *([0.0] * 10), 0.4, 0.4, 0.4, pace=0.01), vad_hold=0.3
        )
    )
    await asyncio.sleep(0.5)
    types = [json.loads(m)["type"] if isinstance(m, str) else "audio" for m in ws.sent]
    assert "finalize" in types
    assert types[types.index("finalize") + 1 :] == []  # nothing after the finalize
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_audio_held_back_is_sent_after_an_empty_answer() -> None:
    ws = FakeSTTSocket()
    task = asyncio.create_task(
        client(ws).listen_turn(
            audio=stream(0.0, 0.4, 0.4, *([0.0] * 6), 0.4, 0.4, 0.4, 0.4, pace=0.01), vad_hold=0.3
        )
    )
    await asyncio.sleep(0.3)
    assert "finalize" in [m["type"] for m in ws.messages()]
    before = len([m for m in ws.sent if isinstance(m, bytes)])
    await ws.inbox.put(json.dumps({"type": "transcript", **transcript("")}))  # nothing was said
    ws.on_finalize = transcript("then I said more")
    await asyncio.sleep(0.5)
    after = len([m for m in ws.sent if isinstance(m, bytes)])
    assert after > before  # the held audio went out once the server said the turn was empty
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_stale_message_is_ignored_by_the_stitched_decode() -> None:
    ws = FakeSTTSocket(on_finalize=transcript("play something by taylor swift"))
    await ws.inbox.put(json.dumps({"type": "transcript", **transcript("a late end of turn")}))
    heard = await asyncio.wait_for(client(ws).hear_pcm(block(0.4) * 4), timeout=5)
    assert heard.text == "play something by taylor swift"
