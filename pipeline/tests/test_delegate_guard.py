import asyncio
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema

from voice_pipeline import agent_tools, question


class Params:
    def __init__(self, task: str) -> None:
        self.arguments = {"task": task, "say": "On it."}
        self.tool_call_id = "x"
        self.delivered: list[Any] = []

        async def cb(result: Any, *, properties: Any = None) -> None:
            self.delivered.append(result)

        self.result_callback = cb


def wrapped(started: list[str]) -> Any:
    async def inner(params: Any) -> None:
        started.append(params.arguments["task"])
        await params.result_callback('{"job": "job1", "status": "started"}')

    schema = FunctionSchema(
        name="delegate",
        description="d",
        properties={"task": {"type": "string"}},
        required=["task"],
        handler=inner,
    )
    return agent_tools._recorded(schema, []).handler


def test_a_destructive_task_asks_first_and_runs_after_a_yes(monkeypatch) -> None:
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0.0)
    monkeypatch.setattr(agent_tools, "_pending_destructive", None)
    started: list[str] = []
    handler = wrapped(started)
    question.current = "remove all the files except the game"
    p = Params("Delete all folders in apps except radiohead_surf")
    asyncio.run(handler(p))
    assert started == [] and p.delivered == [agent_tools.ASK_DESTRUCTIVE]
    question.current = "yes, go ahead"
    p2 = Params("Delete all folders in apps except radiohead_surf")
    asyncio.run(handler(p2))
    assert started == ["Delete all folders in apps except radiohead_surf"]


def test_a_cancelled_turn_starts_no_job(monkeypatch) -> None:
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0.5)
    started: list[str] = []
    handler = wrapped(started)
    question.current = "build me a game"

    async def run() -> None:
        task = asyncio.create_task(handler(Params("Build a small game")))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert started == []
