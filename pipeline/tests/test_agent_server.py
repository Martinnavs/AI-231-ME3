import asyncio
import json
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_pipeline.agent_server import AgentServer


class FakeAgent:
    """Streams two pieces; `slow` waits before finishing so the client can hang up first."""

    def __init__(self, record: list[dict[str, Any]], slow: bool = False) -> None:
        self.record = record
        self.slow = slow
        self.asked: list[list[dict[str, Any]]] = []
        self.cancelled = asyncio.Event()

    async def ask(self, messages: list[dict[str, Any]], *, reset: bool, on_text: Any) -> dict:
        self.asked.append(messages)
        self.record.append({"intent": "LIGHT_ON", "slot": None})
        await on_text("Lights ")
        if self.slow:
            await self.cancelled.wait()
        await on_text("on.")
        return {
            "status": "ok",
            "text": "Lights on.",
            "first_call_s": 0.1,
            "first_text_s": 0.2,
            "end_s": 0.3,
        }

    async def cancel(self) -> None:
        self.cancelled.set()


async def _serve(agent: FakeAgent, record: list[dict[str, Any]], log: Path) -> tuple[Any, str]:
    server = AgentServer(agent, record, log)
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    port = next(iter(ws_server.sockets)).getsockname()[1]
    return ws_server, f"ws://127.0.0.1:{port}"


async def test_streams_text_then_done_and_logs(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = FakeAgent(record)
    ws_server, url = await _serve(agent, record, tmp_path / "log.jsonl")
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "lights on"}]
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": messages}))
        events = [json.loads(await ws.recv()) for _ in range(3)]
    ws_server.close()
    assert events == [{"text": "Lights "}, {"text": "on."}, {"done": True}]
    assert agent.asked == [[{"role": "user", "content": "lights on"}]]
    row = json.loads((tmp_path / "log.jsonl").read_text())
    assert row["question"] == "lights on"
    assert row["calls"] == [{"intent": "LIGHT_ON", "slot": None}]
    assert row["cancelled"] is False


async def test_client_hanging_up_cancels_the_turn(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = FakeAgent(record, slow=True)
    ws_server, url = await _serve(agent, record, tmp_path / "log.jsonl")
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "tell me a story"}]}))
        assert json.loads(await ws.recv()) == {"text": "Lights "}
    await asyncio.wait_for(agent.cancelled.wait(), 2.0)
    for _ in range(50):
        if (tmp_path / "log.jsonl").exists():
            break
        await asyncio.sleep(0.02)
    ws_server.close()
    assert json.loads((tmp_path / "log.jsonl").read_text())["cancelled"] is True
