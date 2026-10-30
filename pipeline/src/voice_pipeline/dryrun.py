"""Stress dry run of the agent (native Gemini, smart_home + MCP tools) over a fixed prompt set.

    LAN_SPEECH_TOKEN=... uv run python -m voice_pipeline.dryrun [--limit N] [--live URL]
        [--run-id ID] [--pi-url URL]

Prompts that expect a tool the agent does not have (the Pi's, when it is off) are skipped
and listed.

One pipeline and one docs server for the whole run; each prompt starts a fresh conversation.
Per turn it records the tool calls, the reply, and the time to the first tool call and to the
first spoken text; then it scores the calls against the prompt's expectation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from voice_pipeline.agent import MODEL, Agent, build_llm, close_llm_sessions
from voice_pipeline.agent_tools import PI_URL, build_tools, runnable, standard_servers

ROOT = Path(__file__).resolve().parents[2]
# A turn counts as finished at the first spoken answer, but the model may still make a call in a
# later round; those late calls used to land in the next prompt's row. Wait for quiet first.
SETTLE_S = 2.5
SETTLE_MAX_S = 20.0
FREE_TEXT = {"CREATE_REMINDER"}
LEAK = re.compile(
    r"function\s*=|<function|\"name\"\s*:|\"intent\"\s*:|smart_home\s*[(>]|</?[A-Z_]{3,}>|\[TOOL_CALLS\]",
    re.IGNORECASE,
)


def _key(call: dict[str, Any]) -> tuple[str, str | None]:
    if "tool" in call:
        return f"tool:{call['tool']}", None
    slot = call.get("slot")
    return str(call.get("intent")), str(slot).lower() if slot else None


def _matches(calls: list[dict[str, Any]], expect: list[dict[str, Any]]) -> bool:
    got = Counter(_key(c) for c in calls)
    want = Counter(_key(e) for e in expect)
    for key in [k for k in got if k[0].startswith("tool:")]:
        got[key] = 1  # repeated searches in one turn count once
    if got == want:
        return True
    free = [k for k in want if k[0] in FREE_TEXT]
    for key in free:  # free text: either slot may contain the other
        found = next(
            (
                g
                for g in got
                if g[0] == key[0] and key[1] and g[1] and (key[1] in g[1] or g[1] in key[1])
            ),
            None,
        )
        if found is not None:
            got[key] += got.pop(found)
    return got == want


def score_turn(calls: list[dict[str, Any]], expect: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        "exact": _matches(calls, expect),
        "intent": {_key(c)[0] for c in calls} == {_key(e)[0] for e in expect},
        "false_call": not expect and bool(calls),
    }


def apply_tool_text(tools: Any, texts: dict[str, str]) -> None:
    """Replace tool descriptions by name; the key `__say__` replaces the `say` parameter's text."""
    for tool in tools.standard_tools:
        if tool.name in texts:
            tool._description = texts[tool.name]
        if "__say__" in texts and "say" in tool.properties:
            tool.properties["say"] = {**tool.properties["say"], "description": texts["__say__"]}


def leaked(text: str) -> bool:
    return bool(LEAK.search(text))


async def settle(
    record: list[dict[str, Any]], quiet_s: float = SETTLE_S, max_s: float = SETTLE_MAX_S
) -> None:
    """Return once no tool call has been recorded for `quiet_s` seconds (or after `max_s`)."""
    start = last_change = time.monotonic()
    size = len(record)
    while time.monotonic() - start < max_s:
        await asyncio.sleep(0.1)
        if len(record) != size:
            size, last_change = len(record), time.monotonic()
        elif time.monotonic() - last_change >= quiet_s:
            return


async def run_prompt(
    agent: Agent, record: list[dict[str, Any]], prompt: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = []
    for index, (turn, expect) in enumerate(zip(prompt["turns"], prompt["expect"], strict=True)):
        before = len(record)
        result = await agent.ask([{"role": "user", "content": turn}], reset=index == 0)
        on_time = len(record) - before
        await settle(record)
        made = record[before:]
        late = len(made) - on_time
        if result["status"] == "ok" and not result["text"] and not made:
            result["status"] = "empty"
        rows.append(
            {
                "id": prompt["id"],
                "kind": prompt["kind"],
                "turn": index,
                "calls": made,
                "late_calls": late,
                "expect": expect,
                **result,
                **score_turn(made, expect),
                "leaked": leaked(result["text"]),
            }
        )
        if result["status"] != "ok":
            break
    return rows


def _p50(values: list[float]) -> str:
    return f"{statistics.median(values):.2f}s" if values else "-"


def summarize(rows: list[dict[str, Any]]) -> str:
    lines = [
        f"{'kind':<15}{'n':>4}{'exact':>7}{'intent':>8}{'false':>7}{'leak':>6}{'bad':>5}"
        f"{'spoken (tool)':>15}{'spoken (none)':>15}"
    ]
    groups: dict[str, list[dict[str, Any]]] = {"ALL": rows}
    for row in rows:
        groups.setdefault(row["kind"], []).append(row)
    for kind, group in sorted(groups.items()):
        ok = [r for r in group if r["status"] == "ok"]
        tool = [r["first_text_s"] for r in ok if r["calls"] and r["first_text_s"]]
        plain = [r["first_text_s"] for r in ok if not r["calls"] and r["first_text_s"]]
        lines.append(
            f"{kind:<15}{len(ok):>4}{sum(r['exact'] for r in ok):>7}"
            f"{sum(r['intent'] for r in ok):>8}{sum(r['false_call'] for r in ok):>7}"
            f"{sum(r['leaked'] for r in ok):>6}{len(group) - len(ok):>5}"
            f"{_p50(tool):>15}{_p50(plain):>15}"
        )
    return "\n".join(lines)


async def run(
    limit: int | None,
    live: str | None,
    run_id: str,
    system_file: Path,
    pi_url: str | None,
    model: str = MODEL,
    prompts_file: Path = ROOT / "config/eval/agent_prompts.jsonl",
    tool_text: Path | None = None,
) -> int:
    lines = prompts_file.read_text().splitlines()
    prompts = [json.loads(line) for line in lines if line.strip()][:limit]
    out = ROOT / "out/agent_dryrun" / run_id
    out.mkdir(parents=True, exist_ok=True)
    record: list[dict[str, Any]] = []
    servers, optional = standard_servers(pi_url)
    tools = await build_tools(record, live=live, servers=servers, optional=optional)
    if tool_text is not None:
        apply_tool_text(tools, json.loads(tool_text.read_text()))
    prompts, skipped = runnable(prompts, {t.name for t in tools.standard_tools})
    if skipped:
        print(f"skipped (tool not available): {', '.join(skipped)}")
    agent = Agent(build_llm(system_file.read_text(), model=model), tools)
    await agent.start()
    await agent.ask([{"role": "user", "content": "Say OK."}], reset=True)  # warm-up
    rows: list[dict[str, Any]] = []
    with (out / "rows.jsonl").open("w") as sink:
        for prompt in prompts:
            prompt_rows = await run_prompt(agent, record, prompt)
            if any(r["status"] != "ok" for r in prompt_rows):
                prompt_rows = await run_prompt(agent, record, prompt)
            for row in prompt_rows:
                sink.write(json.dumps(row) + "\n")
            rows.extend(prompt_rows)
    await agent.stop()
    await close_llm_sessions()
    report = summarize(rows)
    (out / "summary.txt").write_text(report + "\n")
    print(report)
    print(f"rows: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--live", default=None, help="ME2 dashboard URL; default records only")
    parser.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_agent.txt")
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    parser.add_argument("--model", default=MODEL, help="the Gemini model to test")
    parser.add_argument("--prompts", type=Path, default=ROOT / "config/eval/agent_prompts.jsonl")
    parser.add_argument(
        "--tool-text", type=Path, default=None, help="JSON of tool name -> description to try"
    )
    args = parser.parse_args(argv)
    return asyncio.run(
        run(
            args.limit,
            args.live,
            args.run_id,
            args.system,
            args.pi_url or None,
            args.model,
            args.prompts,
            args.tool_text,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
