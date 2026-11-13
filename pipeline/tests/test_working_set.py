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


def test_acknowledgements_are_recognised_and_requests_are_not() -> None:
    from voice_pipeline.chain_state import is_acknowledgement, is_answer

    for said in (
        "Yep, we got it.",
        "Got it, thank you.",
        "Got it.",
        "Understood.",
        "Mm-hmm.",
        "Thanks!",
        "No need, ok, got it",
    ):
        assert is_acknowledgement(said) and is_answer(said), said
    for said in (
        "Yes, thank you, build it",
        "Thanks, now add the title",
        "Got it, please make it blue",
        "What is the weather",
        "Yes please",
    ):
        assert not is_acknowledgement(said), said


def test_the_main_file_of_a_job_is_not_its_manifest() -> None:
    from voice_pipeline.agent_tools import main_file

    files = ["apps/p/MANIFEST.json", "apps/p/index.html", "SOURCES.json"]
    assert main_file(files) == "apps/p/index.html"
    assert main_file(["SOURCES.json", "MANIFEST.json", "notes.md"]) == "notes.md"
    assert main_file(["a.py"]) == "a.py" and main_file([]) == ""


def test_side_questions_leave_a_finished_task_alone_but_count_against_research(
    tmp_path: Path,
) -> None:
    done = ChainState(tmp_path / "a")
    done.update("building", subject="s", artifact_path="apps/p/index.html")
    for _ in range(5):  # music, weather, the clock while a page is being refined
        done.touch_unrelated(side=True)
    assert done.active() and done.data["unrelated_turns"] == 0
    research = ChainState(tmp_path / "b")
    research.update("researching", subject="s")
    for _ in range(3):
        research.touch_unrelated(side=True)  # no deliverable: the old rule stands
    assert not research.active()
    done.touch_unrelated()  # a real topic change still counts
    assert done.data["unrelated_turns"] == 1


async def test_an_acknowledgement_starts_no_job_and_a_loose_refinement_still_gets_the_patch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    chain = ChainState(tmp_path)
    chain.update(
        "building", subject="restaurants", items=["Antonio's", "Toyo"], artifact_path="a/i.html"
    )
    chain.mark_done()
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0)
    monkeypatch.setattr(agent_tools, "CHAIN", chain)
    called: list[str] = []

    async def inner(params: Any) -> None:
        called.append("ran")
        await params.result_callback("ok")

    said: list[str] = []

    async def sink(value: Any, *, properties: Any = None) -> None:
        said.append(str(value))

    schema = FunctionSchema(
        name="delegate", description="d", properties={}, required=[], handler=inner
    )
    question.current = "Yep, we got it."
    await _recorded(schema, record).handler(  # type: ignore[misc]
        SimpleNamespace(
            function_name="delegate",
            arguments={"task": "Update the page with the title."},
            result_callback=sink,
            tool_call_id="d",
        )
    )
    question.current = ""
    assert called == [] and said == [agent_tools.NOTHING_TO_DO] and record == []
    # "of the cuisine they have added Taiwan" has no refinement word, the task does
    loose = await _delegate(
        chain,
        "of the cuisine that they have",
        "Update the page: add the cuisine of Antonio's and Toyo.",
        monkeypatch,
    )
    assert "Keep ALL of these items: Antonio's, Toyo." in loose


def test_a_short_question_about_the_weather_or_music_is_a_side_question() -> None:
    from voice_pipeline.chain_state import is_side_question

    for said in (
        "What is the weather in Cebu today?",
        "Play some Taylor Swift",
        "What time is it?",
        "Turn on the lights",
        "Where am I right now?",
    ):
        assert is_side_question(said), said
    for said in (
        "Update the weather table",  # a refinement that mentions the weather
        "What is the capital of France?",
        "Make the page a table sorted by name",
        "Tell me a long story about the weather and the music of the Philippines and its history",
    ):
        assert not is_side_question(said), said


async def test_a_queued_addition_is_announced_by_one_fixed_sentence_and_separate_is_passed_on(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(agent_tools, "DELEGATE_GRACE_S", 0)
    monkeypatch.setattr(agent_tools, "CHAIN", None)
    monkeypatch.setattr(agent_tools, "SCRATCHPAD", None)
    seen: list[dict[str, Any]] = []
    answers = iter(
        [
            json.dumps({"status": "queued", "job": "job1", "message": "queued"}),
            json.dumps({"job": "job2", "status": "started", "message": "running"}),
        ]
    )

    async def inner(params: Any) -> None:
        seen.append(dict(params.arguments))
        await params.result_callback(next(answers))

    said: list[str] = []

    async def sink(value: Any, *, properties: Any = None) -> None:
        said.append(str(value))

    schema = FunctionSchema(
        name="delegate", description="d", properties={}, required=[], handler=inner
    )
    wrapped = _recorded(schema, []).handler
    for args in ({"task": "Also a page about lighthouses."}, {"task": "A page.", "separate": True}):
        await wrapped(  # type: ignore[misc]
            SimpleNamespace(
                function_name="delegate", arguments=args, result_callback=sink, tool_call_id="d"
            )
        )
    assert said == [agent_tools.QUEUED_SAY, agent_tools.DELEGATE_STARTED]
    assert "separate" in agent_tools.QUEUED_SAY and seen[1]["separate"] is True


async def test_a_separate_task_gets_no_update_sentence(tmp_path: Path, monkeypatch: Any) -> None:
    chain = ChainState(tmp_path)
    chain.update("building", subject="snake", items=["Snake"], artifact_path="apps/snake/i.html")
    chain.mark_done()
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
    question.current = "Make that snake page update separate"
    await _recorded(schema, record).handler(  # type: ignore[misc]
        SimpleNamespace(
            function_name="delegate",
            arguments={"task": "Update the snake page", "separate": True},
            result_callback=sink,
            tool_call_id="d",
        )
    )
    question.current = ""
    assert "Update the existing" not in record[-1]["args"]["task"]


async def test_the_delegate_is_not_given_a_publish_task(monkeypatch: Any) -> None:
    monkeypatch.setattr(agent_tools, "publish_configured", lambda path=None: True)
    record: list[dict[str, Any]] = []
    ran: list[str] = []
    said: list[str] = []

    async def inner(params: Any) -> None:
        ran.append("ran")

    async def sink(value: Any, *, properties: Any = None) -> None:
        said.append(str(value))

    schema = FunctionSchema(
        name="delegate", description="d", properties={}, required=[], handler=inner
    )
    for task in ("Commit and push the app.", "Add two articles, then republish it."):
        await _recorded(schema, record).handler(  # type: ignore[misc]
            SimpleNamespace(
                function_name="delegate",
                arguments={"task": task},
                result_callback=sink,
                tool_call_id="d",
            )
        )
    assert ran == [] and record == [] and said == [agent_tools.NOT_DELEGATED] * 2
