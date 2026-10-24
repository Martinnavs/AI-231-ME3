"""How often and how badly does the LLM spike, and would a second request help? (measurement)

    LAN_SPEECH_TOKEN=... uv run python -m voice_pipeline.latency_probe [--minutes 45] [--every 60]

Two independent agents (each its own Gemini connection and MCP servers, the same tools and system
prompt as the agent server) get the same prompt at the same moment, once every `--every` seconds,
rotating through a fixed mix (plain, home command, time, weather, Wikipedia, Pi status). Per pair:
the time to the first sign of an answer (the first tool call or the first text, whichever comes
first) for each agent. If both are slow together, the spike is on the provider's side right then
and a retry would not help; if one is slow and the other is not, a hedged second request would.
Rows go to out/latency_probe/<start>.jsonl; a summary is printed at the end. Read-only prompts
only (no volume changes).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from voice_pipeline.agent import Agent, build_llm
from voice_pipeline.agent_tools import PI_URL, build_tools, standard_servers

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = [
    ("plain", "How are you today?"),
    ("home", "Turn on the lights."),
    ("time", "What time is it in Tokyo?"),
    ("weather", "Will it rain today?"),
    ("wiki", "Who was Marie Curie?"),
    ("pi", "How hot is the Raspberry Pi running?"),
    ("home", "Set a timer for five minutes."),
    ("plain", "Tell me a short fun fact."),
]


async def make_agent(system: str, pi_url: str | None) -> Agent:
    servers, optional = standard_servers(pi_url)
    agent = Agent(build_llm(system), await build_tools([], servers=servers, optional=optional))
    await agent.start()
    await agent.ask([{"role": "user", "content": "Say OK."}], reset=True)  # warm-up
    return agent


def first_sign(result: dict[str, Any]) -> float | None:
    times = [t for t in (result.get("first_call_s"), result.get("first_text_s")) if t]
    return min(times) if times else None


def summarize(rows: list[dict[str, Any]]) -> str:
    pairs = [(r["a"], r["b"]) for r in rows if r["a"] is not None and r["b"] is not None]
    if not pairs:
        return "no complete pairs"
    single = sorted(x for p in pairs for x in p)
    best = sorted(min(p) for p in pairs)

    def pct(xs: list[float], q: float) -> float:
        return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))]

    slow = [p for p in pairs if max(p) > 2.0]
    both = [p for p in slow if min(p) > 2.0]
    lines = [
        f"pairs: {len(pairs)} (one every interval); requests: {len(single)}",
        f"one request, first sign of an answer: p50 {statistics.median(single):.2f}s "
        f"p90 {pct(single, 0.9):.2f}s max {single[-1]:.2f}s; over 2 s: "
        f"{sum(x > 2.0 for x in single)}/{len(single)}; over 4 s: {sum(x > 4.0 for x in single)}",
        f"best of two (a hedged request): p50 {statistics.median(best):.2f}s "
        f"p90 {pct(best, 0.9):.2f}s max {best[-1]:.2f}s",
        f"pairs with a request over 2 s: {len(slow)}; of those, both over 2 s: {len(both)} "
        "(both slow = the provider at that moment; one slow = a second request would rescue it)",
    ]
    by_kind: dict[str, list[float]] = {}
    for r in rows:
        for x in (r["a"], r["b"]):
            if x is not None:
                by_kind.setdefault(r["kind"], []).append(x)
    lines += [
        f"  {k:<8} n={len(v):>3} p50 {statistics.median(v):.2f}s max {max(v):.2f}s"
        for k, v in sorted(by_kind.items())
    ]
    return "\n".join(lines)


async def run(minutes: float, every: float, pi_url: str | None) -> int:
    system = (ROOT / "config/eval/system_agent.txt").read_text()
    a, b = await asyncio.gather(make_agent(system, pi_url), make_agent(system, pi_url))
    out = ROOT / "out/latency_probe" / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    end = time.monotonic() + minutes * 60
    i = 0
    with out.open("w") as sink:
        while time.monotonic() < end:
            started = time.monotonic()
            kind, prompt = PROMPTS[i % len(PROMPTS)]
            msg = [{"role": "user", "content": prompt}]
            ra, rb = await asyncio.gather(a.ask(msg, reset=True), b.ask(msg, reset=True))
            row = {
                "time": time.strftime("%H:%M:%S"), "kind": kind, "prompt": prompt,
                "a": first_sign(ra), "b": first_sign(rb),
                "a_end": ra["end_s"], "b_end": rb["end_s"],
                "a_status": ra["status"], "b_status": rb["status"],
            }  # fmt: skip
            rows.append(row)
            sink.write(json.dumps(row) + "\n")
            sink.flush()
            print(
                f"{row['time']} {kind:<8} a {row['a'] or 0:5.2f}s b {row['b'] or 0:5.2f}s",
                flush=True,
            )
            i += 1
            await asyncio.sleep(max(0.0, every - (time.monotonic() - started)))
    await asyncio.gather(a.stop(), b.stop())
    report = summarize(rows)
    out.with_suffix(".txt").write_text(report + "\n")
    print(report)
    print(f"rows: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--minutes", type=float, default=45.0)
    parser.add_argument("--every", type=float, default=60.0, help="seconds between pairs")
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.minutes, args.every, args.pi_url or None))


if __name__ == "__main__":
    raise SystemExit(main())
