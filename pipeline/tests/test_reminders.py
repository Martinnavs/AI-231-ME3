from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voice_pipeline import agent_tools
from voice_pipeline.reminders import Reminders


def store(tmp_path: Path, now: list[float] | None = None) -> Reminders:
    clock = now if now is not None else [1000.0]
    return Reminders(tmp_path / "r.json", clock=lambda: clock[0])


def test_the_latest_three_come_back_newest_first_with_the_count(tmp_path: Path) -> None:
    now = [1000.0]
    r = store(tmp_path, now)
    assert r.spoken_latest() == "You have no reminders yet."
    for i, text in enumerate(
        ["clean the car", "call Mom", "buy milk", "pay the rent", "water plants"]
    ):
        now[0] += 100 * (i + 1)
        assert r.add(text)
    assert r.latest() == ["water plants", "pay the rent", "buy milk"]
    assert r.spoken_latest() == (
        "You have 5 reminders. The latest three, newest first: "
        "water plants; pay the rent; buy milk."
    )


def test_one_and_two_reminders_are_worded_for_the_ear(tmp_path: Path) -> None:
    now = [1000.0]
    r = store(tmp_path, now)
    r.add("clean the car")
    assert r.spoken_latest() == "You have one reminder: clean the car."
    now[0] += 500
    r.add("call Mom.")
    assert r.spoken_latest() == "You have two reminders, newest first: call Mom; clean the car."


def test_a_double_call_within_a_minute_is_one_reminder_and_later_it_is_new(tmp_path: Path) -> None:
    now = [1000.0]
    r = store(tmp_path, now)
    assert r.add("stay here") and not r.add("stay here")
    now[0] += 120
    assert r.add("stay here") and r.count() == 2
    assert not r.add("   ")


def test_reminders_survive_a_restart_and_a_broken_file_is_empty(tmp_path: Path) -> None:
    store(tmp_path).add("clean the car")
    assert Reminders(tmp_path / "r.json").latest() == ["clean the car"]
    (tmp_path / "r.json").write_text("not json")
    assert Reminders(tmp_path / "r.json").spoken_latest() == "You have no reminders yet."


def test_the_cap_keeps_the_newest_fifty(tmp_path: Path) -> None:
    now = [0.0]
    r = store(tmp_path, now)
    for i in range(60):
        now[0] += 100
        r.add(f"item {i}")
    assert r.count() == 50 and r.latest(1) == ["item 59"]


async def _home_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """The smart_home handler exactly as build_tools hands it to the agent, over a temp store."""
    monkeypatch.setenv("REMINDERS_FILE", str(tmp_path / "reminders.json"))
    tools = await agent_tools.build_tools([], servers=[])
    home = next(t for t in tools.standard_tools if t.name == "smart_home")
    assert home.handler is not None
    return home.handler


async def _call(handler: Any, intent: str, slot: str | None = None) -> list[tuple[Any, Any]]:
    seen: list[tuple[Any, Any]] = []

    async def sink(value: Any, *, properties: Any = None) -> None:
        seen.append((value, properties))

    args = {"intent": intent, **({"slot": slot} if slot else {})}
    await handler(
        SimpleNamespace(
            function_name="smart_home", arguments=args, result_callback=sink, tool_call_id="t"
        )
    )
    return seen


async def test_listing_says_the_stored_reminders_as_they_are_through_the_real_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = await _home_handler(monkeypatch, tmp_path)
    assert (await _call(handler, "LIST_REMINDERS"))[-1][0] == "You have no reminders yet."
    await _call(handler, "CREATE_REMINDER", "clean the car")
    seen = await _call(handler, "LIST_REMINDERS")
    assert seen[-1][0] == "You have one reminder: clean the car."  # a string: it is spoken
    assert seen[-1][1].run_llm is False  # and no second LLM request follows
    assert Reminders(tmp_path / "reminders.json").latest() == ["clean the car"]


async def test_creating_a_reminder_still_returns_the_dict_the_model_said_a_line_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = await _home_handler(monkeypatch, tmp_path)
    seen = await _call(handler, "CREATE_REMINDER", "call Mom")
    assert isinstance(seen[-1][0], dict) and seen[-1][0]["ok"] is True
