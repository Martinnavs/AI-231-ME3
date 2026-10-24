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

    async def ask(
        self, messages: list[dict[str, Any]], *, reset: bool, on_text: Any, on_tool: Any = None
    ) -> dict:
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


class FakeTool:
    """A tool handler like the agent's: records the call and returns a result."""

    def __init__(self, name: str, record: list[dict[str, Any]], result: Any) -> None:
        self.name = name
        self.record = record
        self.result = result

    async def handler(self, params: Any) -> None:
        self.record.append({"tool": self.name, "args": dict(params.arguments)})
        await params.result_callback(self.result)


async def _serve_fast(
    agent: FakeAgent, record: list[dict[str, Any]], log: Path, result: Any
) -> tuple[Any, str]:
    tools = type("Tools", (), {})()
    tools.standard_tools = [FakeTool("smart_home", record, result)]
    server = AgentServer(agent, record, log, tools)
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    port = next(iter(ws_server.sockets)).getsockname()[1]
    return ws_server, f"ws://127.0.0.1:{port}"


async def test_a_common_command_skips_the_llm(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = FakeAgent(record)
    ws_server, url = await _serve_fast(agent, record, tmp_path / "log.jsonl", {"ok": True})
    async with connect(url) as ws:
        await ws.send(
            json.dumps({"messages": [{"role": "user", "content": "Set a timer for ten minutes."}]})
        )
        events = [json.loads(await ws.recv()) for _ in range(2)]
    ws_server.close()
    assert events == [{"text": "Timer set for 10 minutes."}, {"done": True}]
    assert agent.asked == []  # the LLM was not called
    row = json.loads((tmp_path / "log.jsonl").read_text())
    assert row["fast_path"] is True
    assert row["calls"] == [
        {"tool": "smart_home", "args": {"intent": "TIMER", "slot": "10 minutes"}}
    ]


async def test_a_failed_command_says_why(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    failed = {"ok": False, "message": "home dashboard unreachable"}
    ws_server, url = await _serve_fast(FakeAgent(record), record, tmp_path / "log.jsonl", failed)
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "Lights off."}]}))
        first = json.loads(await ws.recv())
    ws_server.close()
    assert first == {"text": "Sorry, that didn't work: home dashboard unreachable."}


async def test_anything_else_still_goes_to_the_llm(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = FakeAgent(record)
    ws_server, url = await _serve_fast(agent, record, tmp_path / "log.jsonl", {"ok": True})
    async with connect(url) as ws:
        await ws.send(
            json.dumps(
                {"messages": [{"role": "user", "content": "Turn on the lights and play music."}]}
            )
        )
        await ws.recv()
    ws_server.close()
    assert agent.asked == [[{"role": "user", "content": "Turn on the lights and play music."}]]
