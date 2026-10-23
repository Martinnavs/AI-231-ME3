"""Serial latency run: one pipeline (one connection) per model, reused across single-turn prompts.

    uv run python -m voice_pipeline.latency --models gemini-lite:system_v2_router.txt,groq-70b

Per prompt it measures, from queueing the user turn: the first tool call, the first spoken text
(after any tool round trip) and the end of the turn. The first prompt per model is a warm-up and
is not counted. The context is replaced before every prompt, so prompts do not see each other.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionUserMessageParam
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesUpdateFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from voice_pipeline.dryrun import MODELS, ROOT, TURN_TIMEOUT_S, build_llm, score_turn
from voice_pipeline.tools.smart_home import SCHEMA, make_handler

PROMPT_IDS = [f"c{i:02d}" for i in range(1, 20)] + ["p05", "n01", "n02"]


class TimingTap(FrameProcessor):
    """Times one turn: first tool call, first text, end of turn."""

    def __init__(self) -> None:
        super().__init__()
        self.done = asyncio.Event()
        self.reset()

    def reset(self) -> None:
        self.done.clear()
        self.started = time.perf_counter()
        self.first_call: float | None = None
        self.first_text: float | None = None
        self.calls_in_response = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        now = time.perf_counter() - self.started
        if isinstance(frame, LLMFullResponseStartFrame):
            self.calls_in_response = False
        elif isinstance(frame, FunctionCallsStartedFrame):
            self.calls_in_response = True
            if self.first_call is None:
                self.first_call = now
        elif isinstance(frame, LLMTextFrame) and self.first_text is None and frame.text.strip():
            self.first_text = now
        elif isinstance(frame, LLMFullResponseEndFrame) and not self.calls_in_response:
            self.done.set()
        await self.push_frame(frame, direction)


async def run_model(model: str, system: str, prompts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    llm = build_llm(model, system)
    llm.register_function("smart_home", make_handler(base_url=None, record=calls))
    pair = LLMContextAggregatorPair(LLMContext(tools=ToolsSchema(standard_tools=[SCHEMA])))
    tap = TimingTap()
    worker = PipelineWorker(Pipeline([pair.user(), llm, tap, pair.assistant()]))
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    running = asyncio.create_task(runner.run())
    rows = []
    for index, prompt in enumerate([prompts[0], *prompts]):
        before = len(calls)
        tap.reset()
        message: ChatCompletionUserMessageParam = {"role": "user", "content": prompt["turns"][0]}
        await worker.queue_frame(LLMMessagesUpdateFrame(messages=[message], run_llm=True))
        try:
            await asyncio.wait_for(tap.done.wait(), TURN_TIMEOUT_S)
            status = "ok"
        except TimeoutError:
            status = "error"
        made = calls[before:]
        if index == 0:
            continue
        rows.append(
            {
                "model": model,
                "id": prompt["id"],
                "status": status,
                "calls": made,
                "first_call_s": tap.first_call,
                "first_text_s": tap.first_text,
                "end_s": time.perf_counter() - tap.started,
                "exact": score_turn(made, prompt["expect"][0])["exact"],
            }
        )
    await worker.queue_frame(EndFrame())
    await running
    return rows


def _stats(values: list[float]) -> str:
    if not values:
        return "-"
    values = sorted(values)
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return f"{statistics.median(values):.2f} / {p90:.2f}"


def summarize(label: str, rows: list[dict[str, Any]]) -> str:
    ok = [r for r in rows if r["status"] == "ok"]
    tool = [r for r in ok if r["calls"]]
    plain = [r for r in ok if not r["calls"]]
    return (
        f"{label:<40} n={len(ok):>2} exact={sum(r['exact'] for r in ok):>2}"
        f"  tool: call {_stats([r['first_call_s'] for r in tool if r['first_call_s']])}"
        f"  spoken {_stats([r['first_text_s'] for r in tool if r['first_text_s']])}"
        f"  | no tool: spoken {_stats([r['first_text_s'] for r in plain if r['first_text_s']])}"
    )


async def run(specs: list[tuple[str, str]], run_id: str) -> int:
    lines = (ROOT / "config/eval/smart_home_prompts.jsonl").read_text().splitlines()
    by_id = {p["id"]: p for p in (json.loads(line) for line in lines if line.strip())}
    prompts = [by_id[i] for i in PROMPT_IDS]
    out = ROOT / "out/smart_home_latency" / run_id
    out.mkdir(parents=True, exist_ok=True)
    report = ["seconds, p50 / p90; 'call' = first tool call, 'spoken' = first reply text"]
    for model, system_name in specs:
        system = (ROOT / "config/eval" / system_name).read_text()
        rows = await run_model(model, system, prompts)
        label = f"{model} + {system_name}"
        with (out / f"{model}-{Path(system_name).stem}.jsonl").open("w") as sink:
            for row in rows:
                sink.write(json.dumps(row) + "\n")
        report.append(summarize(label, rows))
        print(report[-1], flush=True)
    (out / "summary.txt").write_text("\n".join(report) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--models",
        default="gemini-lite:system_v2_router.txt,groq-70b:system_v0_baseline.txt",
        help="comma-separated model[:system prompt file in config/eval]",
    )
    parser.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args(argv)
    specs = []
    for item in args.models.split(","):
        model, _, system_name = item.partition(":")
        if model not in MODELS:
            raise SystemExit(f"unknown model {model}; choose from {', '.join(MODELS)}")
        specs.append((model, system_name or "system_v2_router.txt"))
    return asyncio.run(run(specs, args.run_id))


if __name__ == "__main__":
    raise SystemExit(main())
