"""Chain wiring: web_research is not a spoken-result tool, chain_update and the delegate hooks,
the agent server's steering while a chain is active, and the Case F prompt rules."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_pipeline import agent_tools
from voice_pipeline.agent_server import AgentServer
from voice_pipeline.agent_tools import SPOKEN_RESULT_TOOLS, _recorded, build_tools
from voice_pipeline.chain_state import ChainState
from voice_pipeline.notices import NoticeQueue
from voice_pipeline.router import Routed

STARTED = '{"job":"job20","status":"started","message":"m"}'
PROMPT = Path(__file__).resolve().parents[1] / "config/eval/system_agent.txt"


@pytest.fixture(autouse=True)
def no_chain_after() -> Any:
    agent_tools.reset_turn_lookups()
    yield
    agent_tools.CHAIN = None
    agent_tools.reset_turn_lookups()


def test_web_research_is_not_a_spoken_result_tool() -> None:
    assert "web_research" not in SPOKEN_RESULT_TOOLS and "web_answer" in SPOKEN_RESULT_TOOLS


class FakeServer:
    async def tools(self) -> Any:
        fn = FunctionSchema(
            name="web_research", description="d", properties={}, required=[], handler=_noop
        )
        return SimpleNamespace(standard_tools=[fn])


async def _noop(params: Any) -> None:
    await params.result_callback("results")


async def test_chain_update_is_offered_only_with_a_chain_and_has_no_say(tmp_path: Path) -> None:
    plain = await build_tools([], servers=[FakeServer()])  # type: ignore[list-item]
    assert "chain_update" not in [t.name for t in plain.standard_tools]
    chain = ChainState(tmp_path)
    record: list[dict[str, Any]] = []
    tools = await build_tools(record, servers=[FakeServer()], chain=chain)  # type: ignore[list-item]
    by_name = {t.name: t for t in tools.standard_tools}
    assert "say" not in by_name["chain_update"].properties
    assert "say" in by_name["web_research"].properties  # offered like any MCP tool
    got: list[Any] = []

    async def done(result: Any, *a: Any, **k: Any) -> None:
        got.append((result, k.get("properties")))

    handler = by_name["chain_update"].handler
    assert handler is not None
    args = {"phase": "awaiting_fact_approval", "subject": "Ana", "facts": "one | two\nthree"}
    await handler(SimpleNamespace(arguments=args, result_callback=done))
    assert got == [("ok", None)]  # a normal tool: the model goes on
    assert chain.data["facts"] == ["one", "two", "three"] and chain.active()
    await handler(SimpleNamespace(arguments={"phase": "bogus"}, result_callback=done))
    assert got[1][0].startswith("error")


async def _call(name: str, result: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0)

    async def handler(params: Any) -> None:
        await params.result_callback(result)

    async def sink(value: Any, *, properties: Any = None) -> None:
        return None

    schema = FunctionSchema(name=name, description="d", properties={}, required=[], handler=handler)
    wrapped = _recorded(schema, [])
    assert wrapped.handler is not None
    await wrapped.handler(SimpleNamespace(arguments={}, result_callback=sink))


async def test_a_started_delegate_job_sets_building_and_job_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_tools.CHAIN = chain = ChainState(tmp_path)
    await _call("delegate", STARTED, monkeypatch)
    assert chain.data["phase"] == "building" and chain.data["job_id"] == "job20"


async def test_unrelated_tools_touch_an_active_chain_but_chain_tools_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_tools.CHAIN = chain = ChainState(tmp_path)
    await _call("weather", "sunny", monkeypatch)
    assert chain.data["unrelated_turns"] == 0  # idle: nothing counted
    chain.update("building")
    await _call("web_research", "r", monkeypatch)
    await _call("delegate_status", "s", monkeypatch)
    assert chain.data["unrelated_turns"] == 0
    await _call("weather", "sunny", monkeypatch)
    assert chain.data["unrelated_turns"] == 1


class RecordingAgent:
    def __init__(self) -> None:
        self.asked: list[list[dict[str, Any]]] = []

    async def ask(self, messages: list[dict[str, Any]], *, on_text: Any, **_: Any) -> dict:
        self.asked.append(messages)
        await on_text("ok.")
        return {"status": "ok", "text": "ok.", "first_call_s": 0, "first_text_s": 0, "end_s": 0}

    async def cancel(self) -> None:
        return None


class CountingRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def route(self, history: Any, user: str, names: Any) -> Routed:
        self.calls += 1
        return Routed(reason="test: send to Gemini")


async def _turn(server: AgentServer, text: str) -> None:
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(ws_server.sockets)).getsockname()[1]}"
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": text}]}))
        while "done" not in json.loads(await ws.recv()):
            pass
    ws_server.close()


async def test_an_active_chain_skips_the_router_and_ends_the_user_message_with_the_block(
    tmp_path: Path,
) -> None:
    chain = ChainState(tmp_path)
    chain.update("awaiting_name", subject="me")
    agent, router = RecordingAgent(), CountingRouter()
    server = AgentServer(agent, [], tmp_path / "log.jsonl", router=router, chain=chain)  # type: ignore[arg-type]
    await _turn(server, "Ana Cruz from Ateneo")
    assert router.calls == 0
    sent = agent.asked[0][-1]["content"]
    assert sent == "Ana Cruz from Ateneo\n" + chain.block()
    assert sent.endswith(chain.block()) and len(agent.asked[0]) == 1


async def test_an_idle_chain_sends_exactly_what_it_did_before(tmp_path: Path) -> None:
    agent, router = RecordingAgent(), CountingRouter()
    idle = AgentServer(
        agent,
        [],
        tmp_path / "a.jsonl",
        router=router,
        chain=ChainState(tmp_path),  # type: ignore[arg-type]
    )
    await _turn(idle, "tell me a joke")
    assert router.calls == 1
    agent2, router2 = RecordingAgent(), CountingRouter()
    plain = AgentServer(agent2, [], tmp_path / "b.jsonl", router=router2)  # type: ignore[arg-type]
    await _turn(plain, "tell me a joke")
    assert agent.asked == agent2.asked == [[{"role": "user", "content": "tell me a joke"}]]


def test_a_finished_chain_job_records_its_file(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("building", job_id="job20")
    (tmp_path / "jobs.json").write_text(
        json.dumps(
            {"counter": 20, "jobs": [{"id": "job20", "status": "done", "files": ["a/x.html"]}]}
        )
    )
    notices = tmp_path / "notices.jsonl"
    notices.write_text("")
    server = AgentServer(
        RecordingAgent(),
        [],
        tmp_path / "l.jsonl",
        notices=NoticeQueue(notices),
        chain=chain,  # type: ignore[arg-type]
    )
    with notices.open("a") as sink:
        sink.write(json.dumps({"job": "job20", "text": "Done."}) + "\n")
    server.collect_notices()
    assert chain.data["artifact_path"] == "a/x.html" and chain.data["phase"] == "building"


def test_case_f_has_the_three_rules_and_the_say_exemption() -> None:
    text = PROMPT.read_text()
    assert "call web_research first" in text
    assert "awaiting_name" in text and "do not know who" in text
    assert "awaiting_fact_approval" in text and "Should I build it with only these public" in text
    assert "chain_update has no `say`" in text


def test_the_prompt_grew_by_less_than_1400_characters() -> None:
    text = PROMPT.read_text()
    assert len(text) < 4976 + 1400  # 4976: its size at HEAD 014b424; now 6364
    assert "recent=true" in text


ASSUMPTION = (
    "If a request could mean several things, ask which in one short question naming up to three, "
    'or say "I\'m assuming X" before you answer.'
)


def test_the_assumption_sentence_is_generic_and_short() -> None:
    assert ASSUMPTION in PROMPT.read_text() and len(ASSUMPTION.split()) <= 30
    for word in ("event", "school", "website", "university"):
        assert word not in ASSUMPTION.lower()


def test_case_f_sends_research_to_web_research_and_keeps_the_facts() -> None:
    text = PROMPT.read_text()
    assert "use web_research with recent=true, not web_answer" in text
    assert "chain_update phase researching" in text and "name up to three and ask which" in text


async def test_the_model_cannot_end_a_chain_with_idle(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("building", subject="x")
    tools = await build_tools([], servers=[FakeServer()], chain=chain)  # type: ignore[list-item]
    handler = {t.name: t for t in tools.standard_tools}["chain_update"].handler
    got: list[Any] = []

    async def done(result: Any, *a: Any, **k: Any) -> None:
        got.append(result)

    assert handler is not None
    await handler(SimpleNamespace(arguments={"phase": "idle"}, result_callback=done))
    assert got == ["ok, the task stays open until it finishes or you say cancel"]
    assert chain.active() and chain.data["phase"] == "building"


@pytest.mark.parametrize(
    "phrase",
    ["never mind", "Forget it.", "cancel that", "Cancel it please", "start over", "stop that"],
)
async def test_cancel_words_end_the_chain_and_stop_its_job(tmp_path: Path, phrase: str) -> None:
    chain = ChainState(tmp_path)
    chain.update("building", subject="x", facts=["f"], job_id="job9")
    cancelled: list[Any] = []

    class CancelTool:
        name = "delegate_cancel"

        async def handler(self, params: Any) -> None:
            cancelled.append(dict(params.arguments))
            await params.result_callback({"status": "cancelled"})

    agent, router = RecordingAgent(), CountingRouter()
    server = AgentServer(
        agent,
        [],
        tmp_path / "l.jsonl",
        SimpleNamespace(standard_tools=[CancelTool()]),
        router,  # type: ignore[arg-type]
        chain=chain,
    )
    await _turn(server, phrase)
    assert not chain.active() and chain.data["facts"] == []
    assert cancelled == [{"job": "job9"}] and agent.asked == [] and router.calls == 0


async def test_a_finished_chain_survives_a_follow_up_and_is_cleared_by_a_topic_change(
    tmp_path: Path,
) -> None:
    chain = ChainState(tmp_path)
    chain.update("building", subject="x", facts=["f"], job_id="job9")
    chain.mark_done()
    agent, router = RecordingAgent(), CountingRouter()
    server = AgentServer(agent, [], tmp_path / "l.jsonl", router=router, chain=chain)  # type: ignore[arg-type]
    await _turn(server, "can you read it back to me")
    assert chain.active() and router.calls == 0 and "ACTIVE TASK" in agent.asked[0][-1]["content"]
    await _turn(server, "what is the capital of France")
    assert not chain.active() and chain.data["facts"] == [] and router.calls == 1
    assert agent.asked[1] == [{"role": "user", "content": "what is the capital of France"}]


def test_followup_detection() -> None:
    from voice_pipeline.chain_state import is_followup

    assert is_followup("yes") and is_followup("go ahead please") and is_followup("make it blue now")
    assert not is_followup("what is the capital of France")


async def test_a_second_lookup_in_a_turn_returns_the_first_text_without_calling_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def handler(params: Any) -> None:
        calls.append(params.function_name)
        await params.result_callback(f"result {len(calls)}")

    got: list[Any] = []

    async def sink(value: Any, *, properties: Any = None) -> None:
        got.append(value)

    def wrapped(name: str) -> Any:
        schema = FunctionSchema(
            name=name, description="d", properties={}, required=[], handler=handler
        )
        return _recorded(schema, []).handler

    agent_tools.reset_turn_lookups()
    for name in ("web_research", "web_answer", "wiki_summary"):
        await wrapped(name)(
            SimpleNamespace(
                function_name=name, arguments={}, result_callback=sink, tool_call_id="t"
            )
        )
    assert calls == ["web_research"]
    assert got[0] == "result 1"
    assert got[1] == got[2] == "(already looked up this turn) result 1"
    await wrapped("weather")(
        SimpleNamespace(
            function_name="weather", arguments={}, result_callback=sink, tool_call_id="t"
        )
    )
    assert calls[-1] == "weather"  # another tool family is not limited
    agent_tools.reset_turn_lookups()
    await wrapped("web_answer")(
        SimpleNamespace(
            function_name="web_answer", arguments={}, result_callback=sink, tool_call_id="t"
        )
    )
    assert calls[-1] == "web_answer"  # a new turn looks up again


async def test_a_failed_lookup_is_not_remembered() -> None:
    results = iter(["error: down", "fine"])
    calls: list[int] = []

    async def handler(params: Any) -> None:
        calls.append(1)
        await params.result_callback(next(results))

    async def sink(value: Any, *, properties: Any = None) -> None:
        return None

    schema = FunctionSchema(
        name="web_answer", description="d", properties={}, required=[], handler=handler
    )
    wrapped = _recorded(schema, []).handler
    agent_tools.reset_turn_lookups()
    for _ in range(2):
        await wrapped(
            SimpleNamespace(
                function_name="web_answer", arguments={}, result_callback=sink, tool_call_id="t"
            )
        )
    assert len(calls) == 2
    agent_tools.reset_turn_lookups()
