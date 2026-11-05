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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from voice_pipeline import fast_path
from voice_pipeline.agent import (
    MODEL,
    SAY_DELAY_SLOW,
    SAY_DELAY_TODAY,
    Agent,
    build_llm,
    close_llm_sessions,
)
from voice_pipeline.agent_tools import (
    BUILTIN_SEARCH_REPLACES,
    IDLE_DELEGATE_TOOLS,
    PI_URL,
    _recorded,
    build_tools,
    runnable,
    standard_servers,
    with_builtin_search,
)
from voice_pipeline.search_count import LEDGER, USD_PER_SEARCH, SearchCounter

ROOT = Path(__file__).resolve().parents[2]
# A turn counts as finished at the first spoken answer, but the model may still make a call in a
# later round; those late calls used to land in the next prompt's row. Wait for quiet first.
SETTLE_S = 2.5
# Spend guard. gemini-3.1-flash-lite list price per million tokens (input, output); a request
# with no
# token count is billed at the worst case. The dry runs of 2026-10-30 used the live agent's whole
# Gemini credit because nothing counted them (docs/POSTMORTEM-2026-10-30-gemini-credit.md).
USD_PER_M_TOKENS = (0.25, 1.50)
USD_PER_REQUEST_LOW, USD_PER_REQUEST_HIGH = 0.0007, 0.0012
REQUESTS_PER_TURN = 2.5  # measured 2026-10-30: 177 requests for 72 turns
STOP_AT = 0.9
EMPTY_LIMIT = 5
SETTLE_MAX_S = 20.0
FREE_TEXT = {"CREATE_REMINDER"}
LEAK = re.compile(
    r"function\s*=|<function|\"name\"\s*:|\"intent\"\s*:|smart_home\s*[(>]|</?[A-Z_]{3,}>|\[TOOL_CALLS\]",
    re.IGNORECASE,
)


class BudgetStop(Exception):
    pass


def estimate_usd(turns: int) -> tuple[float, float]:
    """(low, high) dollars for `turns` turns, before the first request."""
    requests = turns * REQUESTS_PER_TURN
    return requests * USD_PER_REQUEST_LOW, requests * USD_PER_REQUEST_HIGH


@dataclass
class Budget:
    """Dollars spent by every run that shares one ledger file, against a cap."""

    cap: float
    ledger: Path
    spent: float = 0.0

    def __post_init__(self) -> None:
        if self.ledger.exists():
            self.spent = float(json.loads(self.ledger.read_text())["usd"])

    @property
    def remaining(self) -> float:
        return STOP_AT * self.cap - self.spent

    @staticmethod
    def cost(usage: dict[str, int]) -> float:
        """Dollars for `usage`; Google Search queries (the model's own searches) are priced at
        the rate beyond the free allowance, which is the worst case."""
        searches = usage.get("searches", 0) * USD_PER_SEARCH
        if (
            usage["prompt"] + usage["completion"] > 0
        ):  # all prompt tokens at the full price: an upper bound
            return (
                searches
                + (
                    usage["prompt"] * USD_PER_M_TOKENS[0]
                    + usage["completion"] * USD_PER_M_TOKENS[1]
                )
                / 1e6
            )
        return searches + usage["requests"] * USD_PER_REQUEST_HIGH

    def check(self, usage: dict[str, int]) -> float:
        total = self.spent + self.cost(usage)
        if total >= STOP_AT * self.cap:
            raise BudgetStop(f"${total:.3f} of the ${self.cap:.2f} cap")
        return total

    def commit(self, run_id: str, usage: dict[str, int]) -> None:
        data: dict[str, Any] = (
            json.loads(self.ledger.read_text())
            if self.ledger.exists()
            else {"usd": 0.0, "runs": []}
        )
        data["usd"] = round(data["usd"] + self.cost(usage), 6)
        data["runs"].append({"id": run_id, "usd": round(self.cost(usage), 6), **usage})
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps(data, indent=1))
        self.spent = data["usd"]


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


CANNED_LOOKUP = "It is a well-known thing; here is a short, plain answer."


def stub_lookups(tools: ToolsSchema, record: list[dict[str, Any]]) -> ToolsSchema:
    """Make web_answer and wiki_summary answer with a canned sentence and no network, still
    recorded as calls: the old prompt's choice of tool is what a dry run measures, not the web."""
    stubbed = []
    for tool in tools.standard_tools:
        if tool.name in BUILTIN_SEARCH_REPLACES:

            async def canned(params: Any) -> None:
                await params.result_callback(CANNED_LOOKUP)

            plain = {k: v for k, v in tool.properties.items() if k != "say"}
            tool = _recorded(
                FunctionSchema(
                    name=tool.name,
                    description=tool.description,
                    properties=plain,
                    required=tool.required,
                    handler=canned,
                ),
                record,
            )
        stubbed.append(tool)
    return ToolsSchema(standard_tools=stubbed)


def lookup_expected(expect: list[dict[str, Any]]) -> bool:
    """The prompt's expectation includes a web lookup (web_answer or wiki_summary)."""
    return any(e.get("tool") in BUILTIN_SEARCH_REPLACES for e in expect)


def builtin_expect(expect: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The expectation for a model that searches itself: a lookup call is no longer made."""
    return [e for e in expect if e.get("tool") not in BUILTIN_SEARCH_REPLACES]


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
    agent: Agent,
    record: list[dict[str, Any]],
    prompt: dict[str, Any],
    counter: SearchCounter | None = None,
) -> list[dict[str, Any]]:
    """`counter`: the model searches itself, so a lookup call is not expected and the searches it
    ran are counted per turn. A prompt's `search` (true/false/null) says whether a search is wanted;
    absent, it is wanted when the expectation has a web lookup."""
    rows = []
    for index, (turn, expect) in enumerate(zip(prompt["turns"], prompt["expect"], strict=True)):
        before = len(record)
        searched_before = counter.searches if counter is not None else 0
        result = await agent.ask([{"role": "user", "content": turn}], reset=index == 0)
        on_time = len(record) - before
        await settle(record)
        wanted = prompt.get("search", lookup_expected(expect))
        if counter is not None:
            expect = builtin_expect(expect)
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
                **(
                    {"searches": counter.searches - searched_before, "wanted_search": wanted}
                    if counter is not None
                    else {}
                ),
            }
        )
        if result["status"] != "ok":
            break
    return rows


def _spent(agent: Agent, counter: SearchCounter | None) -> dict[str, int]:
    """Requests and tokens so far, plus the searches the model ran for itself."""
    return {**agent.tap.usage, "searches": counter.searches if counter is not None else 0}


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
    if any("searches" in r for r in rows):  # the model searched for itself
        judged = [r for r in rows if r["status"] == "ok" and r.get("wanted_search") is not None]
        want = [r for r in judged if r["wanted_search"]]
        other = [r for r in judged if not r["wanted_search"]]
        lines.append(
            f"searches: {sum(r.get('searches', 0) for r in rows)} in "
            f"{sum(1 for r in rows if r.get('searches'))} of {len(rows)} turns; "
            f"wanted and searched {sum(1 for r in want if r['searches'])}/{len(want)}; "
            f"not wanted but searched {sum(1 for r in other if r['searches'])}/{len(other)}"
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
    budget: Budget | None = None,
    allow_partial: bool = False,
    builtin_search: bool = False,
    stub: bool = False,
    search_ledger: Path | None = LEDGER,
    hide_idle_delegate: bool = False,
    say_delays: dict[str, float] | None = None,
) -> int:
    """`builtin_search`: the model searches the web itself (its Google Search, counted per turn
    and priced into the budget); `stub`: web_answer and wiki_summary return a canned sentence."""
    lines = prompts_file.read_text().splitlines()
    prompts = [json.loads(line) for line in lines if line.strip()][:limit]
    out = ROOT / "out/agent_dryrun" / run_id
    out.mkdir(parents=True, exist_ok=True)
    record: list[dict[str, Any]] = []
    servers, optional = standard_servers(pi_url)
    tools = await build_tools(record, live=live, servers=servers, optional=optional)
    if tool_text is not None:
        apply_tool_text(tools, json.loads(tool_text.read_text()))
    if stub:
        tools = stub_lookups(tools, record)
    prompts, skipped = runnable(prompts, {t.name for t in tools.standard_tools})
    if skipped:
        print(f"skipped (tool not available): {', '.join(skipped)}")
    turns = sum(len(p["turns"]) for p in prompts)
    low, high = estimate_usd(turns)
    print(
        f"{len(prompts)} prompts, {turns} turns: about ${low:.2f}-${high:.2f} "
        f"(about {int(turns * REQUESTS_PER_TURN)} requests)"
    )
    if budget is not None:
        print(
            f"budget: ${budget.spent:.3f} spent of a ${budget.cap:.2f} cap, "
            f"${budget.remaining:.3f} left before the stop"
        )
        if high > budget.remaining and not allow_partial:
            print(
                "not started: the high estimate does not fit in what is left "
                "(--allow-partial to run anyway)"
            )
            return 3
    system = system_file.read_text()
    counter: SearchCounter | None = None
    llm = None
    if builtin_search:  # the model needs the date to know what can have changed
        system = system.replace(
            "{today}", f"{datetime.now(ZoneInfo(fast_path.TIMEZONE)):%A, %B %-d, %Y}"
        )
        llm = build_llm(system, model=model, builtin_search=True)
        counter = SearchCounter(search_ledger)
        counter.attach(llm)
    agent = Agent(
        llm or build_llm(system, model=model),
        with_builtin_search(tools) if builtin_search else tools,
        usage=True,
        say_delays=say_delays,
    )
    if hide_idle_delegate:  # no delegated job exists in a dry run
        agent.hide(IDLE_DELEGATE_TOOLS)
    await agent.start()
    await agent.ask([{"role": "user", "content": "Say OK."}], reset=True)  # warm-up
    rows: list[dict[str, Any]] = []
    code, empty = 0, 0
    with (out / "rows.jsonl").open("w") as sink:
        for prompt in prompts:
            prompt_rows = await run_prompt(agent, record, prompt, counter)
            if any(r["status"] != "ok" for r in prompt_rows):
                prompt_rows = await run_prompt(agent, record, prompt, counter)
            for row in prompt_rows:
                sink.write(json.dumps(row) + "\n")
            rows.extend(prompt_rows)
            empty = empty + 1 if all(r["status"] != "ok" for r in prompt_rows) else 0
            if empty >= EMPTY_LIMIT:
                print(
                    f"stopped: {EMPTY_LIMIT} prompts in a row got no answer; "
                    "check the Gemini balance and quota"
                )
                code = 2
                break
            if budget is not None:
                try:
                    budget.check(_spent(agent, counter))
                except BudgetStop as stop:
                    print(f"stopped: {stop}; the rows so far are kept")
                    code = 3
                    break
    await agent.stop()
    await close_llm_sessions()
    usage = _spent(agent, counter)
    print(
        f"used: {usage['requests']} requests, {usage['prompt']} prompt and "
        f"{usage['completion']} output tokens"
        + (f", {usage['searches']} Google searches" if counter is not None else "")
        + f", about ${Budget.cost(usage):.3f} (searches at the paid rate)"
    )
    if budget is not None:
        budget.commit(run_id, usage)
        print(f"ledger: ${budget.spent:.3f} of the ${budget.cap:.2f} cap")
    report = summarize(rows)
    (out / "summary.txt").write_text(report + "\n")
    print(report)
    print(f"rows: {out}")
    return code


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
        "--max-usd",
        type=float,
        default=0.25,
        help="spend cap in dollars for every run that shares --ledger; a run stops at 90% of it",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="JSON file of dollars spent (default: one per run)",
    )
    parser.add_argument(
        "--allow-partial", action="store_true", help="start even if the estimate may not fit"
    )
    parser.add_argument(
        "--tool-text", type=Path, default=None, help="JSON of tool name -> description to try"
    )
    parser.add_argument(
        "--builtin-search",
        action="store_true",
        help="the model searches the web itself (Gemini's Google Search next to the function "
        "tools, in place of web_answer); uses the system_agent_search.txt prompt unless --system "
        "is given; searches are counted and priced at the paid rate in the budget",
    )
    parser.add_argument(
        "--stub-lookups",
        action="store_true",
        help="web_answer and wiki_summary return a canned sentence (no network, no searches)",
    )
    parser.add_argument(
        "--say-delays",
        choices=["default", "today", "slow"],
        default="default",
        help="how long the model's `say` is held per tool: today = weather only, slow = also "
        "web_answer, search_docs and delegate",
    )
    parser.add_argument(
        "--hide-idle-delegate",
        action="store_true",
        help="do not offer delegate_status and delegate_cancel (no job exists in a dry run)",
    )
    parser.add_argument(
        "--search-ledger",
        type=Path,
        default=LEDGER,
        help="JSON-lines ledger of Google searches, shared with the web_answer server",
    )
    args = parser.parse_args(argv)
    if args.builtin_search and args.system == ROOT / "config/eval/system_agent.txt":
        args.system = ROOT / "config/eval/system_agent_search.txt"
    ledger = args.ledger or ROOT / "out/agent_dryrun" / args.run_id / "spend.json"
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
            Budget(args.max_usd, ledger),
            args.allow_partial,
            args.builtin_search,
            args.stub_lookups,
            args.search_ledger,
            args.hide_idle_delegate,
            {"today": SAY_DELAY_TODAY, "slow": SAY_DELAY_SLOW}.get(args.say_delays),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
