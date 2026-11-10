"""P2: the Case F chain protocol lives outside the base prompt and is appended per turn.

The base prompt keeps every rule a first turn needs (delegate mechanics, research routing,
the "me" entry); the multi-turn chain protocol is a block the server appends while a chain
is active, when the router escalates a turn, and when there is no router.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_pipeline.agent_server import AgentServer
from voice_pipeline.chain_state import ChainState
from voice_pipeline.router import Routed

PROMPTS = Path(__file__).resolve().parents[2] / "pipeline" / "config" / "eval"
BASE = (PROMPTS / "system_agent.txt").read_text()
BLOCK = (PROMPTS / "system_agent_case_f.txt").read_text()


def test_cold_start_rules_stay_in_the_base_prompt() -> None:
    for rule in (
        "Call delegate with `task`",
        "it cannot ask you questions",
        "delegate_status says how a task is going",
        "delegate_cancel stops it",
        "open_app with its folder name",
        "delegate for the file directly, never apps/",
        "use web_research with recent=true, not web_answer",
        "call chain_update phase researching with them",
        "call web_research first",
        "call delegate with verified facts and URLs immediately",
        "assume sensible defaults and build autonomously",
        "call chain_update phase awaiting_name",
        "ask their full name and a detail",
        "No personal contacts",
        "chain_update has no `say`",
    ):
        assert rule in BASE, rule


def test_the_multi_turn_protocol_moved_out_of_the_base_prompt() -> None:
    for rule in (
        "call chain_update phase awaiting_fact_approval with the facts",
        'ask "Should I build it with only these public professional details?"',
        "delegate with the facts and URLs verbatim, then chain_update phase building",
    ):
        assert rule not in BASE, rule
        assert rule in BLOCK, rule


def test_no_rule_was_lost_in_the_split() -> None:
    # every clause of the pre-P2 Case F must survive in the base or the block
    clauses = (
        "building a website, app, markdown file or report, solving math with code",
        "written as a complete request with every detail it needs",
        "If the result comes back, say it",
        "say nothing about duration; the result is announced later",
        "Never delegate for simple questions, home commands, weather or time",
        "empty name lists them",
        "offer to make it and delegate for a web version",
        "For text deliverables (markdown, scripts, tables)",
        "say 2-3 facts naming the source",
        "with several candidates, name up to three and ask which",
        "ask one short question first, naming up to three options",
        "prefer the most recent dated result",
        "never speak search results or call chain_update",
        "then say in two short sentences that you do not know who",
        "read back at most three facts naming sources",
        "web_research (never memory)",
        "No personal contacts in the readback",
    )
    joined = BASE + "\n" + BLOCK
    for clause in clauses:
        assert clause in joined, clause


@pytest.fixture
def chain(tmp_path: Path) -> ChainState:
    return ChainState(tmp_path / "state")


def test_steer_appends_state_and_protocol_while_the_chain_runs(
    chain: ChainState, tmp_path: Path
) -> None:
    server = AgentServer(None, [], tmp_path / "log.jsonl", chain=chain, case_f_block="PROTOCOL")
    chain.update("researching", subject="event", facts=["a fact"])
    out = server.steer([{"role": "user", "content": "What is its history?"}], case_f=True)
    assert "[ACTIVE TASK" in out[-1]["content"]
    assert out[-1]["content"].endswith("PROTOCOL")


def test_steer_leaves_an_idle_plain_turn_bare(chain: ChainState, tmp_path: Path) -> None:
    server = AgentServer(None, [], tmp_path / "log.jsonl", chain=chain, case_f_block="PROTOCOL")
    messages = [{"role": "user", "content": "What time is it?"}]
    assert server.steer(messages, case_f=False) == messages


def test_steer_sends_the_protocol_without_a_chain(chain: ChainState, tmp_path: Path) -> None:
    server = AgentServer(None, [], tmp_path / "log.jsonl", chain=chain, case_f_block="PROTOCOL")
    out = server.steer([{"role": "user", "content": "Build me a website."}], case_f=True)
    assert out[-1]["content"] == "Build me a website.\nPROTOCOL"
    assert "[ACTIVE TASK" not in out[-1]["content"]


def test_steer_keeps_state_when_no_protocol_was_loaded(chain: ChainState, tmp_path: Path) -> None:
    server = AgentServer(None, [], tmp_path / "log.jsonl", chain=chain)
    chain.update("researching", subject="event")
    out = server.steer([{"role": "user", "content": "What is its history?"}], case_f=True)
    assert "[ACTIVE TASK" in out[-1]["content"]
    assert "PROTOCOL" not in out[-1]["content"]


def test_steer_ignores_a_last_message_that_is_not_from_the_user(tmp_path: Path) -> None:
    server = AgentServer(None, [], tmp_path / "log.jsonl", case_f_block="PROTOCOL")
    messages = [{"role": "assistant", "content": "sure"}]
    assert server.steer(messages, case_f=True) == messages


class FakeAgent:
    """Records what it was asked; answers once."""

    def __init__(self) -> None:
        self.asked: list[list[dict[str, Any]]] = []

    async def ask(
        self,
        messages: list[dict[str, Any]],
        *,
        reset: bool,
        on_text: Any,
        on_tool: Any = None,
        on_say: Any = None,
    ) -> dict:
        self.asked.append(messages)
        await on_text("Okay.")
        return {
            "status": "ok",
            "text": "Okay.",
            "first_call_s": 0.1,
            "first_text_s": 0.2,
            "end_s": 0.2,
        }

    async def cancel(self) -> None:
        pass


async def _serve(server: AgentServer) -> tuple[Any, str]:
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    port = next(iter(ws_server.sockets)).getsockname()[1]
    return ws_server, f"ws://127.0.0.1:{port}"


async def _one_turn(url: str, user: str) -> None:
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": user}]}))
        for _ in range(2):
            await ws.recv()


async def test_a_chain_turn_receives_state_and_protocol(tmp_path: Path) -> None:
    chain = ChainState(tmp_path / "state")
    chain.update("researching", subject="event", facts=["a fact"])
    agent = FakeAgent()
    server = AgentServer(agent, [], tmp_path / "log.jsonl", chain=chain, case_f_block="PROTOCOL")
    ws_server, url = await _serve(server)
    await _one_turn(url, "What is its history?")
    ws_server.close()
    content = agent.asked[0][-1]["content"]
    assert "[ACTIVE TASK" in content
    assert content.endswith("PROTOCOL")


async def test_an_idle_turn_without_a_router_still_receives_the_protocol(
    tmp_path: Path,
) -> None:
    # no router: nothing filters the turn, so the protocol goes with it
    agent = FakeAgent()
    server = AgentServer(agent, [], tmp_path / "log.jsonl", case_f_block="PROTOCOL")
    ws_server, url = await _serve(server)
    await _one_turn(url, "What is its history?")
    ws_server.close()
    content = agent.asked[0][-1]["content"]
    assert content.endswith("PROTOCOL")
    assert "[ACTIVE TASK" not in content


class DecliningRouter:
    """A router that cannot answer: every turn goes to Gemini."""

    async def route(self, history: Any, user: str, tools: set[str]) -> Routed:
        return Routed(reason="not a home question", seconds=0.05, lines="none|")


async def test_a_turn_the_router_declines_receives_the_protocol(tmp_path: Path) -> None:
    agent = FakeAgent()
    server = AgentServer(
        agent,
        [],
        tmp_path / "log.jsonl",
        router=DecliningRouter(),
        case_f_block="PROTOCOL",
    )
    ws_server, url = await _serve(server)
    await _one_turn(url, "Build me a website about Stanford.")
    ws_server.close()
    assert agent.asked, "the declined turn must reach the agent"
    content = agent.asked[0][-1]["content"]
    assert content.endswith("PROTOCOL")
    assert "[ACTIVE TASK" not in content
