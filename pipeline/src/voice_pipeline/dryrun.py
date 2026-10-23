"""Dry run of the smart_home tool: prompts through a text-only Pipecat pipeline, scored per model.

uv run python -m voice_pipeline.dryrun --models groq-8b,gemini-lite [--limit N] [--live URL]
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

from openai.types.chat import ChatCompletionUserMessageParam
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.workers.runner import WorkerRunner

from voice_pipeline.tools.smart_home import SCHEMA, make_handler

ROOT = Path(__file__).resolve().parents[2]
# name -> (secret dir, model id, OpenRouter provider slug, or None for Google's native API)
MODELS: dict[str, tuple[str, str, str | None]] = {
    "groq-8b": ("openrouter", "meta-llama/llama-3.1-8b-instruct", "Groq"),
    "groq-70b": ("openrouter", "meta-llama/llama-3.3-70b-instruct", "Groq"),
    # Native Pipecat GoogleLLMService. Gemini's OpenAI-compatible endpoint is not usable with
    # tools from Pipecat: it needs each call's thought_signature sent back, which Pipecat's
    # OpenAI adapter drops (see docs/SMART-HOME-TOOL.md).
    "gemini-lite": ("google-ai-studio", "gemini-3.1-flash-lite", None),
    "gemini-lite-or": ("openrouter", "google/gemini-3.1-flash-lite", "google-ai-studio"),
}
LEAK = re.compile(
    r"function\s*=|<function|\"name\"\s*:|\"intent\"\s*:|smart_home\s*[(>]|</?[A-Z_]{3,}>|\[TOOL_CALLS\]",
    re.IGNORECASE,
)
TURN_TIMEOUT_S = 20.0


def read_key(secret_dir: str) -> str:
    folder = Path.home() / ".secrets" / secret_dir
    for name in ("api-key.txt", "api_key.txt"):
        path = folder / name
        if path.exists():
            return "".join(path.read_text().split())
    raise SystemExit(f"no key in ~/.secrets/{secret_dir}/")


def build_llm(model: str, system: str) -> OpenRouterLLMService | GoogleLLMService:
    secret_dir, model_id, provider = MODELS[model]
    if provider is not None:
        return OpenRouterLLMService(
            api_key=read_key(secret_dir),
            settings=OpenRouterLLMService.Settings(
                model=model_id,
                system_instruction=system,
                provider={"order": [provider], "allow_fallbacks": False},
            ),
        )
    return GoogleLLMService(
        api_key=read_key(secret_dir),
        settings=GoogleLLMService.Settings(model=model_id, system_instruction=system),
    )


class TurnTap(FrameProcessor):
    """Collects one turn's text and first-token time; sets `done` when the turn ends."""

    def __init__(self) -> None:
        super().__init__()
        self.done = asyncio.Event()
        self.text: list[str] = []
        self.started = 0.0
        self.first: float | None = None
        self.calls_in_response = False

    def begin(self) -> None:
        self.done.clear()
        self.text = []
        self.started = time.perf_counter()
        self.first = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self.calls_in_response = False
        elif isinstance(frame, (LLMTextFrame, FunctionCallsStartedFrame)):
            if self.first is None:
                self.first = time.perf_counter() - self.started
            if isinstance(frame, LLMTextFrame):
                self.text.append(frame.text)
            else:
                self.calls_in_response = True
        elif isinstance(frame, LLMFullResponseEndFrame) and not self.calls_in_response:
            self.done.set()
        await self.push_frame(frame, direction)


async def run_prompt(
    model: str, prompt: dict[str, Any], system: str, live: str | None
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    llm = build_llm(model, system)
    llm.register_function("smart_home", make_handler(base_url=live, record=calls))
    pair = LLMContextAggregatorPair(LLMContext(tools=ToolsSchema(standard_tools=[SCHEMA])))
    tap = TurnTap()
    worker = PipelineWorker(Pipeline([pair.user(), llm, tap, pair.assistant()]))
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    running = asyncio.create_task(runner.run())
    rows = []
    for index, (turn, expect) in enumerate(zip(prompt["turns"], prompt["expect"], strict=True)):
        before = len(calls)
        tap.begin()
        message: ChatCompletionUserMessageParam = {"role": "user", "content": turn}
        await worker.queue_frame(LLMMessagesAppendFrame(messages=[message], run_llm=True))
        try:
            await asyncio.wait_for(tap.done.wait(), TURN_TIMEOUT_S)
            status = "ok"
        except TimeoutError:
            status = "error"
        text = "".join(tap.text)
        made = calls[before:]
        if status == "ok" and not text and not made:
            status = "empty"
        rows.append(
            {
                "model": model,
                "id": prompt["id"],
                "kind": prompt["kind"],
                "turn": index,
                "status": status,
                "calls": made,
                "expect": expect,
                "text": text,
                "first_s": tap.first,
                "total_s": time.perf_counter() - tap.started,
                **score_turn(made, expect),
                "leaked": leaked(text),
            }
        )
        if status == "error":
            break
    await worker.queue_frame(EndFrame())
    await running
    return rows


def _key(call: dict[str, Any]) -> tuple[str, str | None]:
    slot = call.get("slot")
    return str(call.get("intent")), str(slot).lower() if slot else None


def score_turn(calls: list[dict[str, Any]], expect: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        "exact": Counter(map(_key, calls)) == Counter(map(_key, expect)),
        "intent": Counter(c.get("intent") for c in calls) == Counter(e["intent"] for e in expect),
        "false_call": not expect and bool(calls),
    }


def leaked(text: str) -> bool:
    return bool(LEAK.search(text))


def summarize(rows: list[dict[str, Any]]) -> str:
    lines = [
        f"{'model':<12}{'kind':<15}{'n':>4}{'exact':>7}{'intent':>8}{'false':>7}"
        f"{'leak':>6}{'bad':>5}{'first p50':>11}"
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["model"], row["kind"]), []).append(row)
        groups.setdefault((row["model"], "ALL"), []).append(row)
    for (model, kind), group in sorted(groups.items()):
        ok = [r for r in group if r["status"] == "ok"]
        firsts = [r["first_s"] for r in ok if r["first_s"] is not None]
        p50 = f"{statistics.median(firsts):.2f}s" if firsts else "-"
        lines.append(
            f"{model:<12}{kind:<15}{len(ok):>4}{sum(r['exact'] for r in ok):>7}"
            f"{sum(r['intent'] for r in ok):>8}{sum(r['false_call'] for r in ok):>7}"
            f"{sum(r['leaked'] for r in ok):>6}{len(group) - len(ok):>5}{p50:>11}"
        )
    return "\n".join(lines)


async def run(
    models: list[str], limit: int | None, live: str | None, run_id: str, system_file: Path
) -> int:
    prompts = [
        json.loads(line)
        for line in (ROOT / "config/eval/smart_home_prompts.jsonl").read_text().splitlines()
        if line.strip()
    ][:limit]
    system = system_file.read_text()
    out = ROOT / "out/smart_home_dryrun" / run_id
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for model in models:
        with (out / f"{model}.jsonl").open("w") as sink:
            for prompt in prompts:
                prompt_rows = await run_prompt(model, prompt, system, live)
                if any(r["status"] != "ok" for r in prompt_rows):
                    prompt_rows = await run_prompt(model, prompt, system, live)
                for row in prompt_rows:
                    sink.write(json.dumps(row) + "\n")
                rows.extend(prompt_rows)
    report = summarize(rows)
    (out / "summary.txt").write_text(report + "\n")
    print(report)
    print(f"rows: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--models", default="groq-8b,gemini-lite,groq-70b")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--live", default=None, help="ME2 dashboard URL; default records only")
    parser.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--system", type=Path, default=ROOT / "config/eval/system_v2_router.txt")
    args = parser.parse_args(argv)
    models = args.models.split(",")
    for model in models:
        if model not in MODELS:
            raise SystemExit(f"unknown model {model}; choose from {', '.join(MODELS)}")
    return asyncio.run(run(models, args.limit, args.live, args.run_id, args.system))


if __name__ == "__main__":
    raise SystemExit(main())
