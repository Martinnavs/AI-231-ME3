"""Publishing to GitHub Pages needs a spoken yes that code checks, not the model."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema

from voice_pipeline import agent_tools, question

READY = f"{agent_tools.PUBLISH_READY} demo: 2 files, 1 KB, public at https://a.github.io/r/demo/."


class Params:
    def __init__(self, app: str = "demo", dry_run: bool = False) -> None:
        self.arguments: dict[str, Any] = {"app": app, "dry_run": dry_run, "say": "One moment."}
        self.tool_call_id = "x"
        self.delivered: list[Any] = []

        async def cb(result: Any, *, properties: Any = None) -> None:
            self.delivered.append(result)

        self.result_callback = cb


def wrapped(calls: list[dict[str, Any]], result: str = READY) -> Any:
    async def inner(params: Any) -> None:
        calls.append(dict(params.arguments))
        await params.result_callback(result)

    schema = FunctionSchema(
        name="publish_pages",
        description="d",
        properties={"app": {"type": "string"}},
        required=["app"],
        handler=inner,
    )
    return agent_tools._recorded(schema, []).handler


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools, "_pending_publish", None)


def run(handler: Any, params: Params, said: str) -> Params:
    question.current = said
    asyncio.run(handler(params))
    return params


def test_the_first_call_is_a_dry_run_that_asks_and_publishes_nothing() -> None:
    calls: list[dict[str, Any]] = []
    p = run(wrapped(calls), Params(dry_run=False), "publish the demo")  # the model said false
    assert calls == [{"app": "demo", "dry_run": True}]
    assert p.delivered == [READY + agent_tools.PUBLISH_ASK]


def test_a_yes_for_the_same_app_publishes_for_real() -> None:
    calls: list[dict[str, Any]] = []
    handler = wrapped(calls)
    run(handler, Params(), "publish the demo")
    p = run(handler, Params(dry_run=True), "yes, go ahead")  # the model's own value is overridden
    assert calls[-1] == {"app": "demo", "dry_run": False}
    assert p.delivered == [READY]
    assert agent_tools._pending_publish is None


def test_a_yes_is_used_up_by_one_publish() -> None:
    calls: list[dict[str, Any]] = []
    handler = wrapped(calls)
    run(handler, Params(), "publish it")
    run(handler, Params(), "yes")
    run(handler, Params(), "yes")  # a second call: asks again, does not publish again
    assert [c["dry_run"] for c in calls] == [True, False, True]


def test_a_call_without_a_yes_in_the_users_words_stays_a_dry_run() -> None:
    calls: list[dict[str, Any]] = []
    handler = wrapped(calls)
    run(handler, Params(), "publish the demo")
    run(handler, Params(), "what's the weather")  # the model calls again on its own
    assert [c["dry_run"] for c in calls] == [True, True]


def test_a_yes_for_another_app_does_not_publish_this_one() -> None:
    calls: list[dict[str, Any]] = []
    handler = wrapped(calls)
    run(handler, Params("demo"), "publish the demo")
    run(handler, Params("snake"), "yes")
    assert [c["dry_run"] for c in calls] == [True, True]


def test_an_old_question_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    handler = wrapped(calls)
    run(handler, Params(), "publish the demo")
    app, asked = agent_tools._pending_publish or ("", 0.0)
    monkeypatch.setattr(
        agent_tools, "_pending_publish", (app, asked - agent_tools.PUBLISH_YES_S - 1)
    )
    run(handler, Params(), "yes")
    assert [c["dry_run"] for c in calls] == [True, True]


def test_a_refusal_is_spoken_and_does_not_arm_a_yes() -> None:
    calls: list[dict[str, Any]] = []
    handler = wrapped(calls, "I couldn't publish demo: it has no index.html at its top.")
    p = run(handler, Params(), "publish the demo")
    assert p.delivered == ["I couldn't publish demo: it has no index.html at its top."]
    assert agent_tools._pending_publish is None
    run(handler, Params(), "yes")
    assert [c["dry_run"] for c in calls] == [True, True]


def test_the_tool_is_offered_only_when_a_repository_is_configured(tmp_path: Path) -> None:
    cfg = tmp_path / "publish.toml"
    assert not agent_tools.publish_configured(cfg)
    cfg.write_text('repo = ""\n')
    assert not agent_tools.publish_configured(cfg)
    cfg.write_text('repo = "https://github.com/a/b"\n')
    assert agent_tools.publish_configured(cfg)
    assert "publish_pages" in agent_tools.SPOKEN_RESULT_TOOLS


def test_the_shipped_config_offers_nothing_and_the_rule_stays_small() -> None:
    assert not agent_tools.publish_configured(agent_tools.REPO_ROOT / "config" / "publish.toml")
    assert len(agent_tools.PUBLISH_RULE) < 400
