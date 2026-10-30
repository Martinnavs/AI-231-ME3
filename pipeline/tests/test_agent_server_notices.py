import json
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_pipeline.agent_server import AgentServer
from voice_pipeline.notices import NoticeQueue


class QuickAgent:
    async def ask(
        self, messages: list[dict[str, Any]], *, reset: bool, on_text: Any, **_: Any
    ) -> dict:
        await on_text("Lights ")
        await on_text("on.")
        return {
            "status": "ok",
            "text": "Lights on.",
            "first_call_s": 0.1,
            "first_text_s": 0.2,
            "end_s": 0.3,
        }

    async def cancel(self) -> None:
        return None


async def _turn(url: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "lights on"}]}))
        while True:
            event = json.loads(await ws.recv())
            out.append(event)
            if event.get("done") or "error" in event:
                return out


async def test_a_finished_job_is_spoken_after_the_next_answer_once(tmp_path: Path) -> None:
    path, log = tmp_path / "notices.jsonl", tmp_path / "log.jsonl"
    path.write_text("")
    server = AgentServer(QuickAgent(), [], log, notices=NoticeQueue(path))
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(ws_server.sockets)).getsockname()[1]}"
    with path.open("a") as sink:
        sink.write(json.dumps({"job": "job1", "text": "The site is ready."}) + "\n")
    assert await _turn(url) == [
        {"text": "Lights "},
        {"text": "on."},
        {"text": "Update on your earlier request: The site is ready.", "flush": True},
        {"done": True},
    ]
    assert await _turn(url) == [{"text": "Lights "}, {"text": "on."}, {"done": True}]
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[0]["notices"] == ["The site is ready."] and rows[1]["notices"] == []
    ws_server.close()
    await ws_server.wait_closed()
