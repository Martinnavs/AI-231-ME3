"""Run models through the benchmark tasks and score the sandbox afterwards.

  uv run --offline --group laptop python .scratch/onit-tools/bench/run_bench.py --arms class-qwen --tasks smoke
  ... --plan            print what would run, spend nothing
  ... --oracle / --null validate the scorers with no model

Spend is capped per run (--max-usd) and per pool (Ledger, stops at 90%); keys are read from files at run
time and never printed. Run folders live under ~/.cache/onit-bench (a folder you own: git refuses others).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "probe"))

import probe  # noqa: E402  (credentials and the model table)
from tasks import TASKS, Task  # noqa: E402
import tasks_hard  # noqa: E402,F401  registers H1-H4
import tasks_stress  # noqa: E402,F401  registers X1-X6
import tasks_limit  # noqa: E402,F401  registers D1 D2 B1 A1 P1 P2 R1 K1-K4 O1 O2

from voice_agent.agent.mcp_clients import McpClientManager  # noqa: E402
from voice_agent.agent.subagent import Ledger, Limits, Model, RunResult, run_task  # noqa: E402
from voice_agent.config import McpServerSpec  # noqa: E402

RUNS = Path.home() / ".cache" / "onit-bench" / "runs"
CAPS = {"gemini": 1.5, "openrouter": 3.0, "qwen": 1000.0}
CACHED = {"gemini-3.8-flash": 0.075, "gemini-3.1-flash-lite": 0.025, "haiku-5.5": 0.01, "glm-5.3-flash": 0.03,
          "deepseek-v4.1-flash": 0.006, "minimax-m3.1-flash": 0.015, "qwen3.8-flash": 0.016,
          "mimo-v2.6-flash": 0.014, "glm-5.3-flashx": 0.09}


def arm_model(arm: str) -> tuple[str, Model, str]:
    """(pool, Model, api key) for an arm; the key is never printed."""
    pool, url, model_id, prices, extra = probe.MODELS[arm]
    key, base = probe.credentials(pool)
    url = url or base
    if pool == "qwen":
        reply = httpx.get(url + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        model_id = reply.json()["data"][0]["id"]
    return pool, Model(url, model_id, prices[0], prices[1], CACHED.get(arm), extra), key


def select(spec: str) -> list[Task]:
    if spec == "all":
        return list(TASKS.values())
    if spec == "limit":
        return [t for t in TASKS.values() if t.kind == "limit"]
    if spec == "stress":
        return [t for t in TASKS.values() if t.kind == "stress"]
    if spec == "smoke":
        return [t for t in TASKS.values() if t.smoke]
    return [TASKS[t] for t in spec.split(",")]


async def run_one(arm: str, task: Task, rep: int, run_id: str, limits: Limits, ledger: Ledger | None,
                  client: httpx.AsyncClient | None = None, model_info: tuple[str, Model, str] | None = None) -> dict[str, Any]:
    pool, model, key = model_info or arm_model(arm)
    sandbox = RUNS / run_id / arm / f"{task.id}-{rep}"
    shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True)
    task.setup(sandbox)
    providers: list[str] = []

    async def note_provider(response: httpx.Response) -> None:
        await response.aread()
        if response.status_code == 200:
            providers.append(str(response.json().get("provider", "")))

    http = client or httpx.AsyncClient(timeout=120.0, event_hooks={"response": [note_provider]})
    spec = McpServerSpec(command=sys.executable, args=("-m", "voice_agent.mcp_servers.code", "--root", str(sandbox)))
    async with McpClientManager({"code": spec}) as registry:
        if registry.unavailable:
            raise RuntimeError(f"code server unavailable: {registry.unavailable}")
        tools = registry.subset(["code__"], exclude=["code__publish_site"])
        result: RunResult = await run_task(task.prompt, model, key, tools, limits, client=http, ledger=ledger, pool=pool)
    if client is None:
        await http.aclose()
    checks = await task.score(sandbox, result.summary)
    return {
        "run": run_id, "arm": arm, "task": task.id, "kind": task.kind, "rep": rep, "pass": all(checks.values()),
        "checks": checks, "status": result.status, "steps": result.steps, "seconds": round(result.seconds, 1),
        "first_call_s": None if result.first_call_s is None else round(result.first_call_s, 1),
        "tokens_in": result.tokens_in, "tokens_cached": result.tokens_cached, "tokens_out": result.tokens_out,
        "usd": round(result.usd, 5), "tool_calls": len(result.tool_calls), "json_errors": result.json_errors,
        "repeats": result.repeats, "providers": sorted(set(providers)), "model": model.model_id,
        "summary": result.summary[:200],
    }


async def validate(mode: str) -> int:
    bad = 0
    for task in TASKS.values():
        root = RUNS / f"_{mode}" / task.id
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        task.setup(root)
        summary = await task.oracle(root) if mode == "oracle" else ""
        checks = await task.score(root, summary)
        good = all(checks.values()) == (mode == "oracle")
        bad += not good
        print(f"{task.id:3} {mode:6} {'ok' if good else 'WRONG'} {checks if not good else ''}")
    return bad


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="")
    ap.add_argument("--tasks", default="smoke")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--max-usd", type=float, default=0.15)
    ap.add_argument("--run-id", default=time.strftime("%m%d-%H%M%S"))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--null", action="store_true")
    args = ap.parse_args()
    if args.oracle or args.null:
        return await validate("oracle" if args.oracle else "null")
    arms, tasks = [a for a in args.arms.split(",") if a], select(args.tasks)
    print(f"run {args.run_id}: {len(arms)} arms x {len(tasks)} tasks x {args.reps} reps = {len(arms) * len(tasks) * args.reps} runs")
    if args.plan:
        for arm in arms:
            print(f"  {arm}: pool {probe.MODELS[arm][0]}, tasks {[t.id for t in tasks]}")
        return 0
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    ledger = Ledger(HERE / "spend.json", CAPS)
    limits = Limits(max_steps=args.max_steps, max_usd=args.max_usd)
    for arm in arms:
        info = arm_model(arm)
        for task in tasks:
            for rep in range(args.reps):
                try:
                    row = await run_one(arm, task, rep, args.run_id, limits, ledger, model_info=info)
                except Exception as exc:
                    row = {"run": args.run_id, "arm": arm, "task": task.id, "rep": rep, "pass": False, "status": f"harness-error {type(exc).__name__}"}
                (out / f"{args.run_id}.jsonl").open("a").write(json.dumps(row) + "\n")
                print(f"  {arm:22} {task.id:3} {'PASS' if row['pass'] else 'FAIL'} {row.get('status'):11} steps={row.get('steps', '-')} "
                      f"{row.get('seconds', '-')}s ${row.get('usd', 0):.4f}", flush=True)
                if row.get("status") == "budget":
                    print("pool budget reached; stopping this arm")
                    break
    print({k: round(v, 4) for k, v in ledger.spent.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
