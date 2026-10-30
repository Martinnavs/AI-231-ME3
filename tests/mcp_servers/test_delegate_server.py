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
    config["paths"] = {"state_dir": str(tmp_path / "state"), "sandbox_root": str(tmp_path / "jobs")}
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
    assert out == {"job": "job1", "status": DONE, "summary": "It is 42."}
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
    assert (tmp_path / "jobs" / "job1" / "a.txt").read_text() == "hi\n"
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
    assert (delegator.wait_s, delegator.max_jobs) == (4.0, 2)
    assert (await delegator.delegate("quick"))["status"] == DONE
    assert (await delegator.delegate("slow one", wait_s=0.01))["status"] == "started"
    assert (await delegator.delegate("slow two", wait_s=0.01))["status"] == "started"
    busy = await delegator.delegate("slow three", wait_s=0.01)
    assert busy["status"] == "busy" and len(delegator.table.jobs) == 3
    release.set()
    await asyncio.gather(*delegator.tasks.values())
    assert (await delegator.delegate("quick"))["status"] == DONE
