"""Search evaluation: does a lookup tool return the fact? Recent, old, local and global cases.

    .venv/bin/python scripts/search_eval.py [--tool answer|research|both] [--only id] [--keywords]
    .venv/bin/python scripts/search_eval.py --dry          # fakes, no network (what the tests run)

Each case (config/eval/search_cases.jsonl) lists words the result must contain (`a|b` for either).
It runs the real tools in process, web_answer (Google-grounded as in production, Tavily as the
fallback; `--no-grounding` for Tavily only) and web_research (Tavily raw results): a case costs one
grounded search or one or two Tavily credits, no model tokens. `--keywords` sends the short keyword
query instead of the question, the way a person searching by hand writes it. Pass means every
required word is in the result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "config/eval/search_cases.jsonl"
Caller = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class Case:
    id: str
    span: str
    question: str
    keywords: str
    must: list[str]


def load_cases(path: Path = CASES) -> list[Case]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [Case(r["id"], r["span"], r["question"], r["keywords"], r["must"]) for r in rows]


def passes(text: str, must: Sequence[str]) -> bool:
    low = text.lower()
    return all(any(alt.lower() in low for alt in item.split("|")) for item in must)


async def run(
    cases: Sequence[Case], tools: Sequence[str], call: Caller, keywords: bool = False
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        for tool in tools:
            query = case.keywords if keywords else case.question
            args: dict[str, Any] = {"query": query}
            if tool == "web_research":
                args["recent"] = case.span.startswith("recent")
            started = time.perf_counter()
            text = await call(tool, args)
            rows.append(
                {
                    "id": case.id,
                    "span": case.span,
                    "tool": tool,
                    "pass": passes(text, case.must),
                    "seconds": round(time.perf_counter() - started, 2),
                    "chars": len(text),
                }
            )
    return rows


def table(rows: Sequence[dict[str, Any]]) -> str:
    lines = [f"{'case':<24}{'span':<14}{'tool':<14}{'pass':>5}{'sec':>7}"]
    for r in rows:
        mark = "yes" if r["pass"] else "NO"
        lines.append(f"{r['id']:<24}{r['span']:<14}{r['tool']:<14}{mark:>5}{r['seconds']:>7}")
    for tool in dict.fromkeys(r["tool"] for r in rows):
        mine = [r for r in rows if r["tool"] == tool]
        lines.append(f"{tool}: {sum(r['pass'] for r in mine)}/{len(mine)} pass")
    return "\n".join(lines)


async def dry_call(tool: str, args: dict[str, Any]) -> str:
    return "Jalao Archival 3.15 Bartlett 1908 Ashkin Strickland Data Science Lapu 1521 Canberra"


async def live(
    cases: Sequence[Case], tools: Sequence[str], keywords: bool, grounding: bool = True
) -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "src"))
    from fastmcp import Client

    from voice_agent.mcp_servers.tavily_search import build_server, load_key

    server = build_server(None, load_key(), None, grounding=grounding, today=date.today)
    async with Client(server) as client:

        async def call(tool: str, args: dict[str, Any]) -> str:
            result = await client.call_tool(tool, args)
            return str(result.structured_content["result"])

        return await run(cases, tools, call, keywords)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--tool", choices=["answer", "research", "both"], default="both")
    parser.add_argument("--only")
    parser.add_argument("--keywords", action="store_true")
    parser.add_argument("--dry", action="store_true")
    parser.add_argument(
        "--allow-tavily", action="store_true", help="run although the credits are past the line"
    )
    parser.add_argument(
        "--no-grounding", action="store_true", help="web_answer without Google (Tavily only)"
    )
    parser.add_argument("--cases", type=Path, default=CASES)
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c.id in wanted]
    if not args.dry:
        sys.path.insert(0, str(ROOT / "src"))
        from voice_agent.tavily_budget import credit_status

        used, limit, mode = credit_status()
        print(f"Tavily credits: {used}/{limit} ({mode})")
        if mode != "normal" and not args.allow_tavily:
            print("not started: web_research spends Tavily credits (--allow-tavily)")
            return 4
    tools = {"answer": ["web_answer"], "research": ["web_research"]}.get(
        args.tool, ["web_answer", "web_research"]
    )
    rows = (
        asyncio.run(run(cases, tools, dry_call, args.keywords))
        if args.dry
        else asyncio.run(live(cases, tools, args.keywords, not args.no_grounding))
    )
    print(table(rows))
    return 0 if rows and all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
