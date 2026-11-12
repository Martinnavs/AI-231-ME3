from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voice_pipeline import agent_tools
from voice_pipeline.agent_tools import _recorded
from voice_pipeline.reminders import Reminders
from voice_pipeline.tools.smart_home import load_phonebook, make_handler, make_schema


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


async def _call(intent: str, slot: str | None, reminders: Reminders) -> list[tuple[Any, Any]]:
    handler = make_handler(base_url=None, record=[], reminders=reminders)
    schema = make_schema(load_phonebook())
    seen: list[tuple[Any, Any]] = []

    async def sink(value: Any, *, properties: Any = None) -> None:
        seen.append((value, properties))

    from pipecat.adapters.schemas.function_schema import FunctionSchema

    wrapped = _recorded(
        FunctionSchema(
            name="smart_home",
            description="d",
            properties=schema.properties,
            required=[],
            handler=handler,
        ),
        [],
    ).handler
    assert wrapped is not None
    args = {"intent": intent, **({"slot": slot} if slot else {})}
    await wrapped(
        SimpleNamespace(
            function_name="smart_home", arguments=args, result_callback=sink, tool_call_id="t"
        )
    )
    return seen


async def test_creating_stores_and_listing_says_the_reminders_as_they_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0)
    r = store(tmp_path)
    await _call("CREATE_REMINDER", "clean the car", r)
    assert r.latest() == ["clean the car"]
    seen = await _call("LIST_REMINDERS", None, r)
    assert seen[-1][0] == "You have one reminder: clean the car."
    assert seen[-1][1].run_llm is False  # no second LLM request: the list is the reply
