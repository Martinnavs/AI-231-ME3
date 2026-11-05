from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path
from typing import Any

import httpx
import pytest

from voice_agent.agent.jobs import CANCELLED, DONE, ERROR, JobTable
from voice_agent.agent.subagent import Model, RunResult
from voice_agent.mcp_servers.delegate import CONFIG, Delegator, resolve_model
from voice_agent.sandbox.runner import preflight

needs_sandbox = pytest.mark.skipif(bool(preflight().problems), reason="sandbox not ready")


def make(tmp_path: Path, run: Any, transport: httpx.AsyncBaseTransport | None = None) -> Delegator:
    key = tmp_path / "key.txt"
    key.write_text("sk-test\n")
    config = tomllib.loads(CONFIG.read_text())
    config["paths"] = {
        "state_dir": str(tmp_path / "state"),
        "sandbox_root": str(tmp_path / "jobs"),
        "workspace": str(tmp_path / "workspace"),
        "network": True,
    }
    config["models"]["haiku"]["key_file"] = str(key)
    config["models"]["qwen"] = {**config["models"]["haiku"], "pool": "qwen"}
    return Delegator(config, JobTable(tmp_path / "state"), run, transport)


def notices(delegator: Delegator) -> list[dict[str, str]]:
    path = delegator.table.notices
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def test_a_quick_task_is_answered_in_the_call_and_not_announced(tmp_path: Path) -> None:
    async def quick(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        return RunResult(status="done", summary="It is 42.\nFiles: none", usd=0.001)

    delegator = make(tmp_path, quick)
    out = await delegator.delegate("what is 6*7", wait_s=2.0)
    assert out == {"job": "job1", "status": DONE, "summary": "It is 42.", "workspace_files": []}
    assert notices(delegator) == [] and delegator.table.jobs["job1"].usd == 0.001


async def test_a_slow_task_returns_started_and_is_announced_when_done(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def slow(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        await release.wait()
        return RunResult(status="done", summary="The site is ready.\nFiles: site/index.html")

    delegator = make(tmp_path, slow)
    out = await delegator.delegate("build a site", wait_s=0.05)
    assert out["status"] == "started" and delegator.status()["status"] == "running"
    release.set()
    await delegator.tasks["job1"]
    assert notices(delegator) == [{"job": "job1", "text": "The site is ready."}]
    assert delegator.status("job1")["summary"] == "The site is ready."


async def test_a_failure_uses_the_fallback_then_reports_a_reason(tmp_path: Path) -> None:
    seen: list[str] = []

    async def failing(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        seen.append(pool)
        return RunResult(status="time_limit" if pool == "qwen" else "error")

    delegator = make(tmp_path, failing)
    out = await delegator.delegate("x", wait_s=2.0)
    assert seen == ["openrouter", "qwen"] and out["status"] == ERROR
    assert out["summary"] == "it ran out of time"
    assert str(delegator.table.jobs["job1"].folder).endswith("job1-qwen")


async def test_the_fallback_is_not_used_when_the_default_succeeds(tmp_path: Path) -> None:
    seen: list[str] = []

    async def fine(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        seen.append(pool)
        return RunResult(status="done", summary="ok")

    await make(tmp_path, fine).delegate("x", wait_s=2.0)
    assert seen == ["openrouter"]


async def test_cancel_stops_a_running_task_and_status_handles_no_jobs(tmp_path: Path) -> None:
    async def forever(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        await asyncio.sleep(60)
        return RunResult()

    delegator = make(tmp_path, forever)
    assert delegator.status()["status"] == "none" and delegator.cancel()["status"] == "none"
    await delegator.delegate("x", wait_s=0.05)
    assert delegator.cancel()["status"] == CANCELLED
    with pytest.raises(asyncio.CancelledError):
        await delegator.tasks["job1"]
    assert delegator.status("job1")["status"] == CANCELLED and notices(delegator) == []


async def test_resolve_model_reads_the_key_file_and_the_class_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delegator = make(tmp_path, None)
    async with httpx.AsyncClient() as client:
        model, key = await resolve_model(delegator.config, "haiku", client)
    assert (model.model_id, key, model.extra["reasoning"]) == (
        "anthropic/claude-haiku-5.5",
        "sk-test",
        {"effort": "low"},
    )
    secrets = tmp_path / "onit"
    secrets.mkdir()
    (secrets / "api_key.txt").write_text("class-key")
    (secrets / "host_address.txt").write_text("10.0.0.5:8000")
    monkeypatch.setenv("ONIT_SECRETS_DIR", str(secrets))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen3.8-27B"}]})

    delegator.config["models"]["qwen"] = tomllib.loads(CONFIG.read_text())["models"]["qwen"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model, key = await resolve_model(delegator.config, "qwen", client)
    assert (model.base_url, model.model_id, key) == (
        "http://10.0.0.5:8000/v1",
        "Qwen/Qwen3.8-27B",
        "class-key",
    )
    assert str(seen[0].url) == "http://10.0.0.5:8000/v1/models"


@needs_sandbox
async def test_the_real_runner_works_in_a_sandbox_folder_with_a_scripted_model(
    tmp_path: Path,
) -> None:
    script = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "code__bash",
                                    "arguments": json.dumps({"command": "echo hi > a.txt"}),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.001},
        },
        {
            "choices": [{"message": {"role": "assistant", "content": "Wrote it.\nFiles: a.txt"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 10, "cost": 0.001},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=script.pop(0))

    delegator = make(tmp_path, None, httpx.MockTransport(handler))
    out = await delegator.delegate("write a.txt", wait_s=20.0)
    assert out["status"] == DONE and out["summary"] == "Wrote it."
    assert (tmp_path / "workspace" / "a.txt").read_text() == "hi\n"
    assert not (tmp_path / "jobs" / "job1" / "a.txt").exists()
    assert "Wrote it." in (tmp_path / "jobs" / "job1" / "log.txt").read_text()
    assert out["workspace_files"] == ["a.txt"]
    assert (
        delegator.table.jobs["job1"].files == ["a.txt"]
        and delegator.table.jobs["job1"].usd == 0.002
    )


async def test_a_dropped_call_while_the_job_runs_still_gets_its_notice(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def slow(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        await release.wait()
        return RunResult(status="done", summary="The site is ready.")

    delegator = make(tmp_path, slow)
    call = asyncio.create_task(delegator.delegate("build a site", wait_s=5.0))
    await asyncio.sleep(0.05)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert delegator.inline == set()
    release.set()
    await delegator.tasks["job1"]
    assert notices(delegator) == [{"job": "job1", "text": "The site is ready."}]


async def test_a_dropped_call_after_the_job_finished_still_gets_its_notice(tmp_path: Path) -> None:
    finished = asyncio.Event()

    async def quick(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        finished.set()
        return RunResult(status="done", summary="Done fast.")

    delegator = make(tmp_path, quick)
    call = asyncio.create_task(delegator.delegate("x", wait_s=5.0))
    await finished.wait()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert notices(delegator) == [{"job": "job1", "text": "Done fast."}]


async def test_the_wait_comes_from_the_config_and_jobs_are_capped(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def runner(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        if task == "quick":
            await asyncio.sleep(0.2)
        else:
            await release.wait()
        return RunResult(status="done", summary="ok")

    delegator = make(tmp_path, runner)
    assert (delegator.wait_s, delegator.max_jobs) == (1.0, 2)
    assert (await delegator.delegate("quick"))["status"] == DONE
    assert (await delegator.delegate("slow one", wait_s=0.01))["status"] == "started"
    assert (await delegator.delegate("slow two", wait_s=0.01))["status"] == "started"
    busy = await delegator.delegate("slow three", wait_s=0.01)
    assert busy["status"] == "busy" and len(delegator.table.jobs) == 3
    release.set()
    await asyncio.gather(*delegator.tasks.values())
    assert (await delegator.delegate("quick"))["status"] == DONE


def table_with(tmp_path: Path, tasks: list[str]) -> Delegator:
    async def never(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        return RunResult()

    delegator = make(tmp_path, never)
    for text in tasks:
        job = delegator.table.create(text, "haiku")
        delegator.table.finish(job.id, DONE, f"Finished {job.id}.")
    return delegator


TASKS = [
    "Write a pottery shop landing page",
    "Sort the invoices in a spreadsheet",
    "Write a pottery quiz game",
]


def test_status_resolves_empty_to_newest_and_an_id_to_that_job(tmp_path: Path) -> None:
    delegator = table_with(tmp_path, TASKS)
    assert delegator.status()["job"] == "job3"
    out = delegator.status("job1")
    assert out["job"] == "job1" and out["task"] == TASKS[0]


def test_status_matches_a_description_substring_and_picks_the_newest(tmp_path: Path) -> None:
    delegator = table_with(tmp_path, TASKS)
    assert delegator.status("Pottery")["job"] == "job3"
    assert delegator.status("invoices")["job"] == "job2"


def test_status_matches_by_shared_words_and_falls_back_to_newest(tmp_path: Path) -> None:
    delegator = table_with(tmp_path, TASKS)
    assert delegator.status("the landing page for the pottery shop")["job"] == "job1"
    assert delegator.status("zebra migration")["job"] == "job3"
    assert delegator.status("pottery")["job"] == "job3"


def test_status_with_no_jobs_says_none(tmp_path: Path) -> None:
    assert table_with(tmp_path, []).status("anything")["status"] == "none"


async def test_cancel_resolves_a_description(tmp_path: Path) -> None:
    async def forever(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        await asyncio.sleep(60)
        return RunResult()

    delegator = make(tmp_path, forever)
    await delegator.delegate("Sort the invoices in a spreadsheet", wait_s=0.01)
    await delegator.delegate("Write a pottery quiz game", wait_s=0.01)
    out = delegator.cancel("the invoices spreadsheet")
    assert out["job"] == "job1" and out["status"] == CANCELLED and "invoices" in out["task"]
    assert delegator.table.jobs["job2"].status == "running"
    for worker in delegator.tasks.values():
        worker.cancel()
    await asyncio.gather(*delegator.tasks.values(), return_exceptions=True)


async def test_the_started_message_forbids_a_duration(tmp_path: Path) -> None:
    async def forever(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        await asyncio.sleep(60)
        return RunResult()

    delegator = make(tmp_path, forever)
    out = await delegator.delegate("x", wait_s=0.01)
    assert "Do not say how long" in out["message"]
    delegator.tasks["job1"].cancel()
    await asyncio.gather(*delegator.tasks.values(), return_exceptions=True)


async def test_every_job_gets_the_same_workspace_and_status_lists_its_files(
    tmp_path: Path,
) -> None:
    seen: list[tuple[Path, list[str]]] = []

    async def runner(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        seen.append((folder, sorted(p.name for p in delegator.workspace.iterdir())))
        (delegator.workspace / f"{task}.py").write_text("x")
        return RunResult(status="done", summary="ok")

    delegator = make(tmp_path, runner)
    await delegator.delegate("first")
    second = await delegator.delegate("second")
    assert [files for _, files in seen] == [[], ["first.py"]]
    assert seen[0][0] != seen[1][0] and seen[0][0].parent == tmp_path / "jobs"
    assert second["workspace_files"] == ["first.py", "second.py"]
    assert delegator.status("")["workspace_files"] == ["first.py", "second.py"]


def test_workspace_listing_skips_hidden_and_caches_and_is_capped(tmp_path: Path) -> None:
    delegator = make(tmp_path, None)
    ws = delegator.workspace
    (ws / "__pycache__").mkdir(parents=True)
    (ws / "__pycache__" / "a.pyc").write_text("x")
    (ws / ".hidden").write_text("x")
    for i in range(30):
        (ws / f"f{i:02}.txt").write_text("x")
    listed = delegator.workspace_files()
    assert len(listed) == 20 and listed[0] == "f00.txt" and not any("pyc" in n for n in listed)


async def test_the_real_runner_passes_workspace_network_and_pylibs_to_the_code_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class Fake:
        def __init__(self, specs: dict[str, Any]) -> None:
            captured["spec"] = specs["code"]
            self.unavailable = ["code"]

        async def __aenter__(self) -> Fake:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("voice_agent.mcp_servers.delegate.McpClientManager", Fake)
    delegator = make(tmp_path, None)
    delegator.pylibs = tmp_path / "pylibs"
    await delegator.run_in_sandbox("t", Model("u", "m", 0, 0, None, {}), "k", tmp_path / "j", "p")
    args = list(captured["spec"].args)
    assert args[args.index("--root") + 1] == str(tmp_path / "workspace")
    assert "--network" in args and args[args.index("--pylibs") + 1] == str(tmp_path / "pylibs")


def test_the_tool_text_says_what_the_sandbox_cannot_do(tmp_path: Path) -> None:
    from voice_agent.mcp_servers.delegate import build_server

    tool = asyncio.run(build_server(make(tmp_path, None)).get_tool("delegate"))
    text = tool.description or ""
    assert "no screen" in text and "network" in text and "share one workspace" in text


def test_the_real_config_turns_network_on_and_waits_at_most_two_seconds() -> None:
    config = tomllib.loads(CONFIG.read_text())
    assert config["paths"]["network"] is True and config["limits"]["wait_s"] <= 2


async def test_status_returns_the_whole_result_of_a_finished_job_not_just_what_was_announced(
    tmp_path: Path,
) -> None:
    long = " ".join(f"word{i}" for i in range(70)) + "."

    async def finish(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        return RunResult(status="done", summary=f"{long}\nFiles: flappy.py")

    delegator = make(tmp_path, finish)
    await delegator.delegate("write a flappy bird game", wait_s=2.0)
    out = delegator.status("what happened with the game")
    assert out["status"] == DONE and out["summary"] == long


async def test_a_cut_off_job_is_failed_in_status_and_the_notice_says_why(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def cut(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        await release.wait()
        return RunResult(status="failed", summary="the reply was cut off at the length limit")

    delegator = make(tmp_path, cut)
    out = await delegator.delegate(
        "Create a simple Angry Birds-like game for the browser", wait_s=0.05
    )
    assert out["status"] == "started"
    release.set()
    await asyncio.sleep(0.1)
    status = delegator.status()
    assert status["status"] == "failed" and status["summary"].startswith("the reply was cut off")
    assert notices(delegator) == [
        {
            "job": "job1",
            "text": "The Create a simple Angry Birds-like job didn't finish: "
            "the reply was cut off at the length limit.",
        }
    ]


async def test_a_reworded_repeat_of_a_running_task_is_not_started_twice(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def slow(task: str, model: Model, key: str, folder: Path, pool: str) -> RunResult:
        await release.wait()
        return RunResult(status="done", summary="Done.")

    delegator = make(tmp_path, slow)
    first = "Create a simple Flappy Bird game using Python and pygame, and also produce a "
    first += "self-contained web version at apps/flappy-bird/index.html."
    again = "Create a playable, self-contained Flappy Bird clone in HTML, CSS, and JavaScript. "
    again += "Save the files in a folder named apps/flappy_bird, with the main file index.html."
    assert (await delegator.delegate(first, wait_s=0.05))["status"] == "started"
    out = await delegator.delegate(again, wait_s=0.05)
    assert out["status"] == "already_running" and out["job"] == "job1"
    assert list(delegator.table.jobs) == ["job1"]
    other = await delegator.delegate("List the files in the workspace directory", wait_s=0.05)
    assert other["status"] == "started"  # a different task still starts
    release.set()
    await asyncio.sleep(0.1)
    # once the first has finished the same request may start again
    assert (await delegator.delegate(again, wait_s=0.05))["status"] in ("started", "done")


def test_the_same_task_rule_needs_shared_subject_words() -> None:
    from voice_agent.mcp_servers.delegate import same_task

    assert same_task("Make an Angry Birds game", "Build a simple angry birds clone for the browser")
    assert not same_task(
        "Write a Python script that prints primes", "Write a Python script for sorting"
    )
    assert not same_task("Create a game", "Create a game")  # no distinctive words: not provable
    assert not same_task("Flappy Bird", "Angry Birds")
