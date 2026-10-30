import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_pipeline.dryrun import (
    Budget,
    BudgetStop,
    apply_tool_text,
    estimate_usd,
    leaked,
    score_turn,
    settle,
)

DOCS = {"tool": "search_docs", "args": {"query": "q"}}


def test_score_exact_ignores_order_and_slot_case() -> None:
    calls = [{"intent": "ALARM", "slot": "6:30 am"}, {"intent": "LIGHT_ON", "slot": None}]
    expect = [{"intent": "LIGHT_ON", "slot": None}, {"intent": "ALARM", "slot": "6:30 AM"}]
    assert score_turn(calls, expect) == {"exact": True, "intent": True, "false_call": False}


def test_score_counts_repeated_searches_once() -> None:
    calls = [{"intent": "LIGHT_OFF", "slot": None}, DOCS, DOCS]
    expect = [{"intent": "LIGHT_OFF", "slot": None}, {"tool": "search_docs"}]
    assert score_turn(calls, expect)["exact"] is True


def test_score_free_text_slot_matches_by_containment() -> None:
    calls = [{"intent": "CREATE_REMINDER", "slot": "pick up the laundry at five"}]
    expect = [{"intent": "CREATE_REMINDER", "slot": "pick up the laundry"}]
    assert score_turn(calls, expect)["exact"] is True
    wrong = [{"intent": "TIMER", "slot": "45 minutes and more"}]
    assert score_turn(wrong, [{"intent": "TIMER", "slot": "45 minutes"}])["exact"] is False


def test_score_false_call() -> None:
    assert score_turn([DOCS], []) == {"exact": False, "intent": False, "false_call": True}
    assert score_turn([], []) == {"exact": True, "intent": True, "false_call": False}


def test_leaked() -> None:
    assert leaked('function=smart_home>{"intent": "STOP"}</function>')
    assert leaked("<LIGHT_OFF></LIGHT_OFF>")
    assert not leaked("Sure, the lights are on now.")


def test_apply_tool_text_replaces_descriptions_and_the_say_text() -> None:
    from pipecat.adapters.schemas.function_schema import FunctionSchema

    def schema(name: str) -> FunctionSchema:
        return FunctionSchema(
            name=name,
            description="old",
            properties={
                "q": {"type": "string"},
                "say": {"type": "string", "description": "old say"},
            },
            required=["q"],
        )

    one, two = schema("one"), schema("two")
    apply_tool_text(
        SimpleNamespace(standard_tools=[one, two]), {"one": "new", "__say__": "new say"}
    )
    assert (one.description, two.description) == ("new", "old")
    assert one.properties["say"]["description"] == two.properties["say"]["description"] == "new say"
    assert one.properties["q"] == {"type": "string"}


async def test_settle_waits_for_a_late_call_and_then_returns() -> None:
    record: list[dict[str, str]] = []

    async def late() -> None:
        await asyncio.sleep(0.3)
        record.append({"tool": "web_answer"})

    task = asyncio.create_task(late())
    await settle(record, quiet_s=0.5, max_s=5.0)
    await task
    assert record == [{"tool": "web_answer"}]
    start = asyncio.get_running_loop().time()
    await settle(record, quiet_s=0.2, max_s=5.0)  # nothing more arrives
    assert asyncio.get_running_loop().time() - start < 1.0


def test_the_estimate_scales_with_turns() -> None:
    low, high = estimate_usd(100)
    assert (round(low, 3), round(high, 3)) == (0.175, 0.3)


def test_cost_uses_tokens_when_there_are_some_and_requests_when_there_are_none() -> None:
    tokens = {"requests": 3, "prompt": 1_000_000, "completion": 100_000, "cached": 0}
    assert Budget.cost(tokens) == pytest.approx(0.25 + 0.15)
    assert Budget.cost(
        {"requests": 10, "prompt": 0, "completion": 0, "cached": 0}
    ) == pytest.approx(0.012)


def test_a_budget_stops_at_ninety_percent_and_the_ledger_is_shared_between_runs(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "spend.json"
    first = Budget(0.5, ledger)
    assert first.remaining == pytest.approx(0.45)
    used = {"requests": 100, "prompt": 300_000, "completion": 10_000, "cached": 0}  # $0.09
    assert first.check(used) == pytest.approx(0.09)
    first.commit("run-1", used)
    second = Budget(0.5, ledger)  # a later run on the same ledger starts from what is spent
    assert second.spent == pytest.approx(0.09) and second.remaining == pytest.approx(0.36)
    with pytest.raises(BudgetStop):
        second.check(
            {"requests": 1000, "prompt": 1_500_000, "completion": 0, "cached": 0}
        )  # $0.375 more
    second.commit("run-2", used)
    assert Budget(0.5, ledger).spent == pytest.approx(0.18)
