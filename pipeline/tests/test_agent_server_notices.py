"""R1: finished jobs are pushed on a channel of their own, never appended to a turn's reply."""

import asyncio
import json
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_pipeline import agent_server
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


async def _start(tmp_path: Path) -> tuple[Any, str, Path, AgentServer]:
    path = tmp_path / "notices.jsonl"
    path.write_text("")
    server = AgentServer(QuickAgent(), [], tmp_path / "log.jsonl", notices=NoticeQueue(path))
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    return (
        ws_server,
        f"ws://127.0.0.1:{next(iter(ws_server.sockets)).getsockname()[1]}",
        path,
        server,
    )


def _finish(path: Path, job: str, text: str) -> None:
    with path.open("a") as sink:
        sink.write(json.dumps({"job": job, "text": text}) + "\n")


async def test_a_finished_job_is_never_appended_to_an_unrelated_reply(tmp_path: Path) -> None:
    ws_server, url, path, server = await _start(tmp_path)
    _finish(path, "job1", "The site is ready.")
    assert await _turn(url) == [{"text": "Lights "}, {"text": "on."}, {"done": True}]
    rows = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert "notices" not in rows[0]
    ws_server.close()
    await ws_server.wait_closed()


async def test_the_channel_pushes_a_notice_at_once_and_resends_it_until_acked(
    tmp_path: Path,
) -> None:
    ws_server, url, path, server = await _start(tmp_path)
    async with connect(url) as ws:
        await ws.send(json.dumps({"notices": True}))
        await asyncio.sleep(0.1)
        _finish(path, "job1", "The site is ready.")
        t0 = asyncio.get_running_loop().time()
        pushed = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert pushed == {"notice": {"id": 1, "text": "The site is ready.", "job": "job1"}}
        assert asyncio.get_running_loop().time() - t0 < agent_server.NOTICE_POLL_S + 0.3
    # the client went away without acking (a barge-in, a crash): the next connection gets it again
    async with connect(url) as ws:
        await ws.send(json.dumps({"notices": True}))
        again = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert again == {"notice": {"id": 1, "text": "The site is ready.", "job": "job1"}}
        await ws.send(json.dumps({"ack": 1}))
        await asyncio.sleep(0.1)
    assert server.unacked == {}
    async with connect(url) as ws:  # acked: gone for good
        await ws.send(json.dumps({"notices": True}))
        try:
            await asyncio.wait_for(ws.recv(), 0.5)
            raise AssertionError("an acked notice came back")
        except TimeoutError:
            pass
    ws_server.close()
    await ws_server.wait_closed()


async def test_notices_do_not_wait_for_a_turn_or_block_one(tmp_path: Path) -> None:
    ws_server, url, path, server = await _start(tmp_path)
    async with connect(url) as ws:
        await ws.send(json.dumps({"notices": True}))
        _finish(path, "job1", "One.")
        assert await asyncio.wait_for(ws.recv(), 2)
        assert await _turn(url) == [{"text": "Lights "}, {"text": "on."}, {"done": True}]
    ws_server.close()
    await ws_server.wait_closed()


async def test_the_test_only_delay_pauses_a_matching_turn_and_a_closed_socket_cancels_it(
    tmp_path: Path,
) -> None:
    server = AgentServer(QuickAgent(), [], tmp_path / "log.jsonl")
    server.test_delay = ("tortoise", 0.4)
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(ws_server.sockets)).getsockname()[1]}"
    loop = asyncio.get_running_loop()

    async def timed(text: str) -> float:
        t0 = loop.time()
        async with connect(url) as ws:
            await ws.send(json.dumps({"messages": [{"role": "user", "content": text}]}))
            while "done" not in json.loads(await ws.recv()):
                pass
        return loop.time() - t0

    assert await timed("lights on") < 0.3
    assert await timed("the Tortoise question") >= 0.4
    async with connect(url) as ws:  # the client gives up during the pause: the turn is dropped
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "tortoise"}]}))
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    assert not server.lock.locked()
    ws_server.close()
    await ws_server.wait_closed()
