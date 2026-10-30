from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from voice_agent.agent.mcp_clients import ToolOutcome, ToolSpec
from voice_agent.agent.subagent import Ledger, Limits, Model, run_task, usage_cost

MODEL = Model("https://x.example/v1", "m", price_in=1.0, price_out=2.0)
SPEC = ToolSpec("code__bash", "code", "bash", "run", {"type": "object", "properties": {}})


class FakeTools:
    tools = [SPEC]

    def __init__(self, text: str = "ok", is_error: bool = False) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.text, self.is_error = text, is_error

    async def call(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        return ToolOutcome(self.text, self.is_error, 0.0)


def call(arguments: str, name: str = "code__bash", call_id: str = "c1") -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def reply(
    content: str | None = None, calls: list[dict[str, Any]] | None = None, **usage: Any
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100, **usage},
    }


def client(
    script: list[dict[str, Any] | int | Exception], seen: list[dict[str, Any]] | None = None
) -> httpx.AsyncClient:
    queue = list(script)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return (
            httpx.Response(item, text="bad key sk-secret")
            if isinstance(item, int)
            else httpx.Response(200, json=item)
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_tool_call_then_answer() -> None:
    tools = FakeTools()
    http = client([reply(calls=[call('{"command": "ls"}')]), reply("Done. Files: a.txt")])
    result = await run_task("do it", MODEL, "sk-secret", tools, client=http)
    assert (result.status, result.steps, result.summary) == ("done", 2, "Done. Files: a.txt")
    assert result.tool_calls == ["code__bash"] and tools.calls == [
        ("code__bash", {"command": "ls"})
    ]
    assert result.tokens_in == 2000 and result.first_call_s is not None
    assert result.usd == pytest.approx((2000 * 1.0 + 200 * 2.0) / 1e6)


async def test_bad_json_arguments_are_reported_to_the_model() -> None:
    seen: list[dict[str, Any]] = []
    http = client([reply(calls=[call("{nope")]), reply("fine")], seen)
    result = await run_task("x", MODEL, "k", FakeTools(), client=http)
    assert result.json_errors == 1 and result.status == "done"
    assert seen[1]["messages"][-1]["content"].startswith("error: the arguments")


async def test_identical_consecutive_call_is_not_run_twice() -> None:
    tools = FakeTools()
    same = '{"command": "ls"}'
    http = client(
        [
            reply(calls=[call(same, call_id="1")]),
            reply(calls=[call(same, call_id="2")]),
            reply("ok"),
        ]
    )
    result = await run_task("x", MODEL, "k", tools, client=http)
    assert len(tools.calls) == 1 and result.repeats == 1


async def test_step_limit() -> None:
    script = [reply(calls=[call(json.dumps({"command": str(i)}))]) for i in range(5)]
    result = await run_task(
        "x", MODEL, "k", FakeTools(), Limits(max_steps=3), client=client(script)
    )
    assert (result.status, result.steps) == ("step_limit", 3)


async def test_cost_limit_stops_after_the_step_that_crossed_it() -> None:
    http = client([reply(calls=[call("{}")], cost=0.2), reply("never")])
    result = await run_task("x", MODEL, "k", FakeTools(), Limits(max_usd=0.15), client=http)
    assert (result.status, result.steps, result.usd) == ("cost_limit", 1, 0.2)


async def test_http_error_scrubs_the_key() -> None:
    result = await run_task("x", MODEL, "sk-secret", FakeTools(), client=client([400]))
    assert (
        result.status == "error" and "sk-secret" not in result.summary and "[key]" in result.summary
    )


async def test_dropped_connection_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("voice_agent.agent.subagent.asyncio.sleep", no_sleep)
    drop = httpx.ReadError("dropped")
    ok = await run_task("x", MODEL, "k", FakeTools(), client=client([drop, drop, reply("Fine.")]))
    assert (ok.status, ok.summary) == ("done", "Fine.")
    gone = await run_task("x", MODEL, "k", FakeTools(), client=client([drop, drop, drop]))
    assert gone.status == "error"


async def test_tool_result_is_clamped_and_errors_are_marked() -> None:
    seen: list[dict[str, Any]] = []
    http = client([reply(calls=[call("{}")]), reply("ok")], seen)
    await run_task("x", MODEL, "k", FakeTools("a" * 20000, is_error=True), client=http)
    content = seen[1]["messages"][-1]["content"]
    assert (
        content.startswith("error: a") and content.endswith("…[truncated]") and len(content) < 12100
    )


def test_usage_cost_prefers_billed_cost_and_prices_cached_tokens() -> None:
    assert usage_cost(MODEL, {"cost": 0.5, "prompt_tokens": 9}) == 0.5
    priced = Model("u", "m", price_in=1.0, price_out=2.0, price_cached=0.1)
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 800},
    }
    assert usage_cost(priced, usage) == pytest.approx((200 * 1.0 + 800 * 0.1 + 100 * 2.0) / 1e6)


async def test_ledger_stops_at_ninety_percent_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "spend.json"
    ledger = Ledger(path, {"gemini": 1.0})
    ledger.add("gemini", 0.95)
    assert Ledger(path, {"gemini": 1.0}).spent == {"gemini": 0.95}
    result = await run_task(
        "x", MODEL, "k", FakeTools(), client=client([]), ledger=ledger, pool="gemini"
    )
    assert result.status == "budget" and result.steps == 0
