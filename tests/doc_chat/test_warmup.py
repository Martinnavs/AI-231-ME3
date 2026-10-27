"""The silent warm-up before the first turn: room noise, the speaker, one agent request."""

import argparse
import asyncio
import io
from typing import Any

import numpy as np

from doc_chat.__main__ import Session
from doc_chat.dialogue import ChatHistory
from doc_chat.speech import DocChatSpeechClient


class FakeHub:
    block_ms = 100

    def __init__(self, blocks: list[bytes]) -> None:
        self.blocks_in = list(blocks)
        self.drained = 0

    def drain(self) -> int:
        self.drained += 1
        return 0

    async def get(self) -> bytes:
        if not self.blocks_in:
            await asyncio.sleep(10)
        return self.blocks_in.pop(0)


def _block(level: float) -> bytes:
    return (np.full(1600, int(level * 32768), dtype=np.int16)).tobytes()


def test_the_room_level_is_the_quiet_end_of_what_was_heard() -> None:
    client = DocChatSpeechClient("ws://127.0.0.1:1")
    client.hub = FakeHub([_block(0.004)] * 8 + [_block(0.2)] * 2)  # type: ignore[assignment]
    level = asyncio.run(client.measure_room(1.0))
    assert level is not None and abs(level - 0.004) < 0.0005  # 10th percentile: speech is ignored
    assert client.noise_floor == level


def test_without_a_microphone_there_is_no_room_level() -> None:
    client = DocChatSpeechClient("ws://127.0.0.1:1")
    assert asyncio.run(client.measure_room(1.0)) is None and client.noise_floor is None


class WarmClient:
    tts_sample_rate = 24000
    canceller = None

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.measured = 0

    async def measure_room(self, seconds: float) -> float | None:
        self.measured += 1
        if self.fail:
            raise RuntimeError("no mic")
        return 0.01


def _session(client: WarmClient, streamer: Any = None, play: bool = False) -> Session:
    ns = argparse.Namespace(play=play, llm="agent", play_remote=None, voice="F1")
    s = Session(
        args=ns, client=client, log_file=io.StringIO(), out_dir=None, history=ChatHistory(0)
    )  # type: ignore[arg-type]
    s.streamer = streamer
    return s


def test_the_warm_up_measures_the_room_and_sends_one_request_through_the_agent_path(
    capsys: Any,
) -> None:
    asked: list[str] = []

    def streamer(question: str) -> list[str]:
        asked.append(question)
        return ["OK."]

    client = WarmClient()
    asyncio.run(_session(client, streamer).warm_up())
    assert client.measured == 1 and asked == ["Say OK."]
    assert "warm-up done" in capsys.readouterr().out


def test_a_failing_step_is_reported_and_the_rest_still_runs(capsys: Any) -> None:
    def streamer(question: str) -> list[str]:
        raise ConnectionError("agent server down")

    client = WarmClient(fail=True)
    asyncio.run(_session(client, streamer).warm_up())
    out = capsys.readouterr().out
    assert "room not measured" in out and "agent not reached" in out and "warm-up done" in out
