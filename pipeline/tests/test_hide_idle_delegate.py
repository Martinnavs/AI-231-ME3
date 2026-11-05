import json
from pathlib import Path
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.frame_processor import FrameProcessor

from voice_pipeline.agent import Agent
from voice_pipeline.agent_server import AgentServer
from voice_pipeline.agent_tools import IDLE_DELEGATE_TOOLS, has_delegate_jobs, without_tools


def schema(name: str) -> FunctionSchema:
    return FunctionSchema(name=name, description=name, properties={}, required=[])


TOOLS = ToolsSchema(
    standard_tools=[schema(n) for n in ("delegate", "delegate_status", "delegate_cancel")]
)


def names(tools: Any) -> list[str]:
    return [t.name for t in tools.standard_tools]


def test_without_tools_drops_only_the_named_ones() -> None:
    assert names(without_tools(TOOLS, IDLE_DELEGATE_TOOLS)) == ["delegate"]


def test_has_delegate_jobs(tmp_path: Path) -> None:
    table = tmp_path / "jobs.json"
    assert not has_delegate_jobs(table)
    table.write_text(json.dumps({"counter": 0, "jobs": []}))
    assert not has_delegate_jobs(table)
    table.write_text(json.dumps({"counter": 1, "jobs": [{"id": "job1"}]}))
    assert has_delegate_jobs(table)
    table.write_text("{broken")
    assert has_delegate_jobs(table)  # unreadable: keep the tools


async def test_agent_hide_changes_what_the_model_is_offered() -> None:
    agent = Agent(FrameProcessor(), TOOLS)  # type: ignore[arg-type]
    agent.hide(IDLE_DELEGATE_TOOLS)
    assert names(agent.context.tools) == ["delegate"]
    agent.hide(frozenset())
    assert names(agent.context.tools) == ["delegate", "delegate_status", "delegate_cancel"]


class Recorder:
    def __init__(self) -> None:
        self.hidden: list[frozenset[str]] = []

    def hide(self, names: frozenset[str]) -> None:
        self.hidden.append(names)

    async def ask(self, messages: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "text": "", "first_call_s": 0, "first_text_s": 0, "end_s": 0}

    async def cancel(self) -> None:
        pass


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def recv(self) -> str:
        return json.dumps({"messages": [{"role": "user", "content": "hi"}]})

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def wait_closed(self) -> None:
        import asyncio

        await asyncio.Event().wait()


async def test_server_hides_the_tools_until_a_job_exists(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.json"
    agent = Recorder()
    server = AgentServer(agent, [], tmp_path / "log.jsonl", jobs_file=jobs)
    await server.handle(FakeWs())  # type: ignore[arg-type]
    jobs.write_text(json.dumps({"counter": 1, "jobs": [{"id": "job1"}]}))
    await server.handle(FakeWs())  # type: ignore[arg-type]
    assert agent.hidden == [IDLE_DELEGATE_TOOLS, frozenset()]


async def test_server_without_the_flag_never_touches_the_tools(tmp_path: Path) -> None:
    agent = Recorder()
    await AgentServer(agent, [], tmp_path / "log.jsonl").handle(FakeWs())  # type: ignore[arg-type]
    assert agent.hidden == []
