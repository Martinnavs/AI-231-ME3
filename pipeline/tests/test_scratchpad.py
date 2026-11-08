from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema

from voice_pipeline import agent_tools
from voice_pipeline.agent_tools import _recorded
from voice_pipeline.chain_state import ChainState
from voice_pipeline.scratchpad import HEADER, MAX_BYTES, NOTES_SENTENCE, Scratchpad


def pad(tmp_path: Path) -> Scratchpad:
    return Scratchpad(tmp_path / "scratch" / "NOTES.md", clock=lambda: 0.0)


def test_add_render_urls_and_the_header(tmp_path: Path) -> None:
    notes = pad(tmp_path)
    assert not notes.has_entries() and notes.urls() == set()
    notes.add(
        "web_research", "what is x", "Title | https://a.example/1 | snippet", ["https://b.example"]
    )
    text = notes.render()
    assert text.startswith(HEADER) and "## " in text and "web_research: what is x" in text
    assert notes.urls() == {"https://a.example/1", "https://b.example"}
    assert notes.has_entries()


def test_the_cap_drops_the_oldest_entries_first_and_keeps_the_header(tmp_path: Path) -> None:
    notes = pad(tmp_path)
    for i in range(30):
        notes.add("web_answer", f"q{i}", f"answer {i} " + "x" * 900)
    text = notes.render()
    assert len(text.encode()) <= MAX_BYTES and text.startswith(HEADER)
    assert "q29" in text and "q0:" not in text
    notes.add("web_answer", "huge", "y" * 50_000)
    assert len(notes.render().encode()) <= MAX_BYTES and "huge" in notes.render()


def test_the_write_is_atomic(tmp_path: Path) -> None:
    notes = pad(tmp_path)
    notes.add("web_answer", "q", "a")
    assert [p.name for p in notes.path.parent.iterdir()] == ["NOTES.md"]


def test_reset_keeps_one_previous_copy_and_can_keep_this_turns_entries(tmp_path: Path) -> None:
    notes = pad(tmp_path)
    notes.add("web_answer", "old", "old answer")
    notes.mark_turn()
    notes.add("web_answer", "new", "new answer")
    notes.reset(keep_current_turn=True)
    assert "new answer" in notes.render() and "old answer" not in notes.render()
    assert "old answer" in (notes.path.parent / "NOTES.prev.md").read_text()
    notes.reset()
    assert not notes.has_entries() and notes.render().startswith(HEADER)


def test_a_chain_start_and_end_drive_the_reset(tmp_path: Path) -> None:
    notes = pad(tmp_path)
    chain = ChainState(tmp_path / "state")
    chain.on_change = lambda event: notes.reset(keep_current_turn=event == "start")
    notes.add("web_answer", "stale", "stale")
    notes.mark_turn()
    notes.add("web_research", "fresh", "fresh")
    chain.update("researching", subject="s")
    assert "fresh" in notes.render() and "stale" not in notes.render()
    chain.update("idle")
    assert not notes.has_entries()


@pytest.fixture
def wired(tmp_path: Path) -> Any:
    notes = pad(tmp_path)
    agent_tools.SCRATCHPAD = notes
    agent_tools.reset_turn_lookups()
    yield notes
    agent_tools.SCRATCHPAD = None
    agent_tools.reset_turn_lookups()


async def _run(name: str, result: str, args: dict[str, Any], record: list[dict[str, Any]]) -> None:
    async def handler(params: Any) -> None:
        await params.result_callback(result)

    async def sink(value: Any, *, properties: Any = None) -> None:
        return None

    schema = FunctionSchema(name=name, description="d", properties={}, required=[], handler=handler)
    wrapped = _recorded(schema, record).handler
    assert wrapped is not None
    await wrapped(
        SimpleNamespace(function_name=name, arguments=args, result_callback=sink, tool_call_id="t")
    )


async def test_only_successful_first_lookups_are_noted_and_repeats_are_marked(
    wired: Scratchpad,
) -> None:
    record: list[dict[str, Any]] = []
    await _run("web_answer", "error: down", {"query": "failed one"}, record)
    assert not wired.has_entries()
    await _run("web_research", "Title | https://a.example | text", {"query": "first"}, record)
    await _run(
        "web_answer", "another", {"query": "second"}, record
    )  # same turn: served from memory
    text = wired.render()
    assert "first" in text and "second" not in text and "another" not in text
    assert record[-1].get("cached") is True and "cached" not in record[-2]
    agent_tools.reset_turn_lookups()
    await _run("wiki_summary", "A short summary.", {"topic": "a topic"}, record)
    assert "wiki_summary: a topic" in wired.render()


async def test_the_delegate_task_gets_the_notes_sentence_once_and_only_with_notes(
    wired: Scratchpad, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0)
    record: list[dict[str, Any]] = []
    await _run("delegate", '{"status":"busy"}', {"task": "Make it."}, record)
    assert record[-1]["args"]["task"] == "Make it."  # empty pad: unchanged
    wired.add("web_answer", "q", "a")
    await _run("delegate", '{"status":"busy"}', {"task": "Make it."}, record)
    assert record[-1]["args"]["task"] == f"Make it. {NOTES_SENTENCE}"
    await _run("delegate", '{"status":"busy"}', {"task": f"Make it. {NOTES_SENTENCE}"}, record)
    assert record[-1]["args"]["task"].count(NOTES_SENTENCE) == 1


async def test_without_a_scratchpad_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0)
    agent_tools.SCRATCHPAD = None
    record: list[dict[str, Any]] = []
    await _run("delegate", '{"status":"busy"}', {"task": "Make it."}, record)
    assert record[-1]["args"]["task"] == "Make it."


async def test_chain_update_facts_are_noted(tmp_path: Path, wired: Scratchpad) -> None:
    chain = ChainState(tmp_path / "state")
    schema = agent_tools.chain_update_schema(chain, [])
    got: list[Any] = []

    async def done(result: Any, **k: Any) -> None:
        got.append(result)

    assert schema.handler is not None
    args = {"phase": "researching", "subject": "topic", "facts": "one | two"}
    await schema.handler(SimpleNamespace(arguments=args, result_callback=done))
    assert "facts: topic" in wired.render() and "one\ntwo" in wired.render()
