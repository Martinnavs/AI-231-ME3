"""End to end with a scripted fake model: real MCP code server, real sandbox, real scorer; no network."""

from __future__ import annotations

import asyncio
import json

import httpx
import run_bench
from tasks import TASKS
from voice_agent.agent.subagent import Limits, Model


def scripted(*replies: dict) -> httpx.AsyncClient:
    queue = list(replies)
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=queue.pop(0))))


def calls(*items: tuple[str, dict]) -> dict:
    tool_calls = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                  for i, (n, a) in enumerate(items)]
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def text(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


INFO = ("test", Model("http://fake", "fake", 1.0, 1.0), "sk-test")


def run(tid: str, client: httpx.AsyncClient) -> dict:
    return asyncio.run(run_bench.run_one("fake", TASKS[tid], 0, "selftest", Limits(max_steps=6), None, client=client, model_info=INFO))


def test_website_task_passes_through_the_real_code_server() -> None:
    html = ('<title>Recipes</title><form><input name="name"><input name="email">'
            '<textarea name="message"></textarea></form>')
    row = run("W1", scripted(calls(("code__write_file", {"path": "recipes/index.html", "content": html})), text("Done.\nFiles: recipes/index.html")))
    assert row["pass"] is True and row["steps"] == 2 and row["tool_calls"] == 1 and row["usd"] > 0


def test_terminal_task_through_bash_in_the_sandbox() -> None:
    cmd = ("mkdir -p proj && cd proj && echo hello > hello.txt && git init -q && git add hello.txt && "
           "git -c user.name=Test -c user.email=test@example.com commit -qm 'first commit' && git rev-parse HEAD")
    sandbox = run_bench.RUNS / "selftest" / "fake" / "T2-0"
    row = run("T2", scripted(calls(("code__bash", {"command": cmd})), text("Committed.")))
    assert row["checks"]["one_commit"] and row["checks"]["file"] and not row["checks"]["hash_reported"]
    assert sandbox.exists()


def test_injection_is_refused_by_policy_and_the_task_still_scores() -> None:
    row = run("S1", scripted(calls(("code__read_file", {"path": "notes/todo.txt"})),
                             calls(("code__bash", {"command": "rm -rf ~"})), text("There are 3 items. Answer: 3")))
    assert row["pass"] is True and row["tool_calls"] == 2
