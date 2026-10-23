"""Agent server: the agent behind a local WebSocket, for `doc_chat --llm agent`.

    uv run python -m voice_pipeline.agent_server [--port 8781] [--live URL]

Protocol (the client is src/doc_chat/agent_client.py in the root project), one request per
connection: {"messages": [...]} in; {"text": ...} pieces, then {"done": true} or {"error": ...}
out. The server owns the system prompt, so client system messages are dropped. Requests are
served one at a time; a client that disconnects before "done" cancels its turn. Every turn is
logged to out/agent_server/<start time>.jsonl with its question, tool calls and timings.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from voice_pipeline.agent import Agent, build_llm
from voice_pipeline.agent_tools import build_tools, docs_server

ROOT = Path(__file__).resolve().parents[2]


class AgentServer:
    """Serves `agent` turns over WebSocket and logs each one."""

    def __init__(self, agent: Any, record: list[dict[str, Any]], log: Path) -> None:
        self.agent = agent
        self.record = record
        self.log = log
        self.lock = asyncio.Lock()

    async def handle(self, ws: ServerConnection) -> None:
        request = json.loads(await ws.recv())
        messages = [m for m in request["messages"] if m.get("role") != "system"]

        async def on_text(text: str) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps({"text": text}))

        async with self.lock:
            before = len(self.record)
            turn = asyncio.create_task(self.agent.ask(messages, reset=True, on_text=on_text))
            closed = asyncio.create_task(ws.wait_closed())
            await asyncio.wait({turn, closed}, return_when=asyncio.FIRST_COMPLETED)
            cancelled = not turn.done()
            if cancelled:
                await self.agent.cancel()
            result = await turn
            closed.cancel()
            if not cancelled:
                reply = {"done": True} if result["status"] == "ok" else {"error": "timeout"}
                try:
                    await ws.send(json.dumps(reply))
                except ConnectionClosed:
                    cancelled = True
            row = {
                "time": time.strftime("%H:%M:%S"),
                "question": messages[-1]["content"] if messages else "",
                "calls": self.record[before:],
                "cancelled": cancelled,
                **result,
            }
            with self.log.open("a") as sink:
                sink.write(json.dumps(row) + "\n")


async def run(port: int, live: str | None, system_file: Path) -> int:
    record: list[dict[str, Any]] = []
    tools = await build_tools(record, live=live, docs=docs_server())
    agent = Agent(build_llm(system_file.read_text()), tools)
    await agent.start()
    await agent.ask([{"role": "user", "content": "Say OK."}], reset=True)  # warm-up
    log = ROOT / "out/agent_server" / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    server = AgentServer(agent, record, log)
    async with serve(server.handle, "127.0.0.1", port):
        print(f"agent server on ws://127.0.0.1:{port}, log {log}", flush=True)
        await asyncio.Future()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--live", default=None, help="ME2 dashboard URL; default records only")
    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.port, args.live, args.system))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
