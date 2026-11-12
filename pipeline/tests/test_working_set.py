"""The working set: the things a task is about, kept across refinements (2026-11-12: five cities
became three because each chain_update replaced the list and the agent could not see the page)."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema

from voice_pipeline import agent_tools, question
from voice_pipeline.agent_tools import _recorded, read_manifest, update_sentence
from voice_pipeline.chain_state import ChainState, is_followup, refines


def test_items_are_added_never_replaced_and_removed_on_request(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("researching", subject="cities", items=["Quezon City", "Davao"])
    chain.update("building", items=["Manila", "davao", "Caloocan"])  # davao is not added twice
    assert chain.data["items"] == ["Quezon City", "Davao", "Manila", "Caloocan"]
    chain.update(remove=["davao"])
    assert chain.data["items"] == ["Quezon City", "Manila", "Caloocan"]
    chain.update(facts=["x"], subject="s")  # a facts update leaves the items alone
    assert chain.data["items"] == ["Quezon City", "Manila", "Caloocan"]


def test_the_set_is_capped_and_the_block_shows_items_and_gaps(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("building", subject="s", items=[f"item {i}" for i in range(30)])
    assert len(chain.data["items"]) == 12
    chain.update(gaps=["Caloocan weather", "Taguig weather"], artifact_path="apps/x/index.html")
    block = chain.block()
    assert 'items="item 0 ; item 1' in block
    assert "[ARTIFACT: apps/x/index.html | missing: Caloocan weather ; Taguig weather]" in block


def test_a_refinement_belongs_to_the_finished_task() -> None:
    for said in (
        "Now what I want is number one the population and the weather in one table",
        "Could you try to find the weather for the other two cities",
        "update the website based on their order",
        "sort it by name",
    ):
        assert is_followup(said) and refines(said), said
    assert not is_followup("what is the capital of France") and not refines("play some music")


def test_update_sentence_names_the_file_the_items_and_the_gaps() -> None:
    data = {
        "artifact_path": "apps/c/index.html",
        "items": ["Quezon City", "Manila"],
        "gaps": ["Manila weather"],
    }
    text = update_sentence(data)
    assert text.startswith("Update the existing apps/c/index.html in place")
    assert "Keep ALL of these items: Quezon City, Manila." in text
    assert "known to lack: Manila weather" in text
    assert "Keep ALL" not in update_sentence({"artifact_path": "a.md", "items": [], "gaps": []})


async def _delegate(chain: ChainState, said: str, task: str, monkeypatch: Any) -> str:
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0)
    monkeypatch.setattr(agent_tools, "CHAIN", chain)
    monkeypatch.setattr(agent_tools, "SCRATCHPAD", None)
    record: list[dict[str, Any]] = []

    async def inner(params: Any) -> None:
        await params.result_callback("ok")

    async def sink(value: Any, *, properties: Any = None) -> None:
        return None

    schema = FunctionSchema(
        name="delegate", description="d", properties={}, required=[], handler=inner
    )
    question.current = said
    await _recorded(schema, record).handler(  # type: ignore[misc]
        SimpleNamespace(
            function_name="delegate",
            arguments={"task": task},
            result_callback=sink,
            tool_call_id="d",
        )
    )
    question.current = ""
    return str(record[-1]["args"]["task"])


async def test_a_refinement_delegates_a_patch_that_keeps_every_item(
    tmp_path: Path, monkeypatch: Any
) -> None:
    chain = ChainState(tmp_path)
    chain.update(
        "building",
        subject="largest cities",
        items=["Quezon City", "Manila", "Davao"],
        artifact_path="apps/cities/index.html",
        gaps=["Davao weather"],
    )
    chain.mark_done()
    task = await _delegate(
        chain,
        "Now put the weather and the population in one table",
        "Create a table of the largest cities with population and weather.",
        monkeypatch,
    )
    assert "Update the existing apps/cities/index.html in place" in task
    assert "Keep ALL of these items: Quezon City, Manila, Davao." in task
    assert "known to lack: Davao weather" in task


async def test_a_new_request_with_no_refinement_words_gets_no_update_sentence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    chain = ChainState(tmp_path)
    chain.update(
        "building", subject="largest cities", items=["Manila"], artifact_path="apps/c/index.html"
    )
    task = await _delegate(
        chain, "Make a poster for the largest cities", "Poster for the largest cities.", monkeypatch
    )
    assert "Update the existing" not in task


def test_the_manifest_is_read_only_from_a_plain_small_file_inside_the_workspace(
    tmp_path: Path,
) -> None:
    (tmp_path / "apps" / "c").mkdir(parents=True)
    manifest = tmp_path / "apps" / "c" / "MANIFEST.json"
    manifest.write_text(json.dumps({"items": ["Manila", 7, {"x": 1}], "gaps": ["Davao weather"]}))
    assert read_manifest("apps/c/index.html", tmp_path) == {
        "items": ["Manila", "7"],
        "gaps": ["Davao weather"],
    }
    assert read_manifest("apps/none/index.html", tmp_path) is None
    manifest.write_text("[1, 2]")
    assert read_manifest("apps/c/index.html", tmp_path) is None
    manifest.write_text("x" * 30_000)
    assert read_manifest("apps/c/index.html", tmp_path) is None
    manifest.unlink()
    outside = tmp_path.parent / "elsewhere.json"
    outside.write_text(json.dumps({"items": ["leak"], "gaps": []}))
    manifest.symlink_to(outside)
    assert read_manifest("apps/c/index.html", tmp_path) is None  # a link out is never followed
    outside.unlink()


def test_chain_update_tool_merges_items_and_removes(tmp_path: Path) -> None:
    import asyncio

    chain = ChainState(tmp_path)
    schema = agent_tools.chain_update_schema(chain, [])
    assert {"items", "remove"} <= set(schema.properties)
    got: list[str] = []

    async def done(result: Any, *, properties: Any = None) -> None:
        got.append(str(result))

    def call(args: dict[str, str]) -> Any:
        return SimpleNamespace(arguments=args, result_callback=done)

    asyncio.run(schema.handler(call({"phase": "researching", "items": "Manila | Davao"})))  # type: ignore[misc]
    asyncio.run(schema.handler(call({"phase": "researching", "items": "Cebu", "remove": "Davao"})))  # type: ignore[misc]
    assert chain.data["items"] == ["Manila", "Cebu"] and got == ["ok", "ok"]


class _Agent:
    async def ask(self, messages: Any, *, on_text: Any, **_: Any) -> dict[str, Any]:
        return {"status": "ok", "text": "", "first_call_s": 0, "first_text_s": 0, "end_s": 0}

    async def cancel(self) -> None:
        return None


def test_a_finished_job_copies_its_manifest_into_the_chain(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from voice_pipeline.agent_server import AgentServer

    workspace = tmp_path / "ws"
    (workspace / "apps" / "c").mkdir(parents=True)
    (workspace / "apps" / "c" / "MANIFEST.json").write_text(
        json.dumps({"items": ["Quezon City", "Manila"], "gaps": ["Manila weather"]})
    )
    monkeypatch.setattr(agent_tools, "delegate_workspace", lambda: workspace)
    state = tmp_path / "state"
    chain = ChainState(state)
    chain.update("building", subject="cities", job_id="job1", items=["Davao"])
    (state / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": "job1", "status": "done", "files": ["apps/c/index.html"]}]})
    )
    server = AgentServer(_Agent(), [], tmp_path / "log.jsonl", chain=chain)  # type: ignore[arg-type]
    server.note_chain_job("job1")
    assert chain.data["artifact_path"] == "apps/c/index.html" and chain.data["done"]
    assert chain.data["items"] == ["Davao", "Quezon City", "Manila"]  # added to, not replaced
    assert chain.data["gaps"] == ["Manila weather"]
    assert "missing: Manila weather" in chain.block()
