"""Jev Router vs gemini-3.1-flash-lite on OpenRouter: first-token time, tool choice, cost.

    uv run --group laptop python .scratch/latency-research/jev_check.py [--reps 2] [--budget 0.10]

Same system prompt, same four tools as the agent (smart_home, time, weather, wiki), streamed chat
completions. Per request: time to the first content or tool-call token, total time, the tool
called, the model OpenRouter says answered, and the cost OpenRouter reports. Stops before the
total spend reaches --budget (USD). The key is read from ~/.secrets/openrouter/ at run time and
never printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS = ["typesafe/jev-router", "google/gemini-3.1-flash-lite"]
SYSTEM = (
    "You are a voice assistant for a smart home. Answer in one or two short spoken sentences. "
    "Use smart_home for any home command, get_current_time for the time, weather for weather, "
    "wiki_summary for general-knowledge questions; otherwise answer directly."
)
TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "smart_home", "description": "Control the home: lights, music, timers, thermostat.",
        "parameters": {"type": "object", "properties": {
            "intent": {"type": "string", "enum": ["LIGHT_ON", "LIGHT_OFF", "PLAY_MUSIC", "PAUSE",
                                                   "TIMER", "TEMPERATURE"]},
            "slot": {"type": "string", "description": "value, e.g. '10 minutes' or '24 degrees'"}},
            "required": ["intent"]}}},
    {"type": "function", "function": {
        "name": "get_current_time", "description": "Current time in an IANA time zone.",
        "parameters": {"type": "object", "properties": {"timezone": {"type": "string"}},
                       "required": ["timezone"]}}},
    {"type": "function", "function": {
        "name": "weather", "description": "Weather for a place, today or tomorrow.",
        "parameters": {"type": "object", "properties": {"place": {"type": "string"},
                                                        "day": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "wiki_summary", "description": "Short encyclopedia summary of a subject.",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string"}},
                       "required": ["topic"]}}},
]  # fmt: skip
PROMPTS = [
    ("Turn on the lights.", "smart_home"),
    ("Set a timer for ten minutes.", "smart_home"),
    ("What time is it in London?", "get_current_time"),
    ("Will it rain tomorrow?", "weather"),
    ("Tell me about Blood Meridian.", "wiki_summary"),
    ("Who wrote The Road?", "wiki_summary"),
    ("How are you today?", None),
    ("Thanks, that's all.", None),
]


def read_key() -> str:
    folder = Path.home() / ".secrets" / "openrouter"
    for name in ("api-key.txt", "api_key.txt"):
        if (folder / name).exists():
            return "".join((folder / name).read_text().split())
    raise SystemExit("no key in ~/.secrets/openrouter/")


def one(http: httpx.Client, model: str, prompt: str) -> dict[str, Any]:
    for _attempt in range(2):  # the connection to OpenRouter drops now and then
        try:
            return _one(http, model, prompt)
        except httpx.HTTPError as exc:
            err = f"{type(exc).__name__}: {exc}"
    return {"error": err}


def _one(http: httpx.Client, model: str, prompt: str) -> dict[str, Any]:
    body = {
        "model": model, "stream": True, "max_tokens": 120, "tools": TOOLS,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "usage": {"include": True},
    }  # fmt: skip
    t0 = time.perf_counter()
    first: float | None = None
    tool, served, cost, text = None, None, None, ""
    with http.stream("POST", URL, json=body) as r:
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}: {r.read().decode()[:160]}"}
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            served = chunk.get("model", served)
            if chunk.get("usage"):
                cost = chunk["usage"].get("cost")
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                calls = delta.get("tool_calls") or []
                if (delta.get("content") or calls) and first is None:
                    first = time.perf_counter() - t0
                text += delta.get("content") or ""
                for c in calls:
                    tool = (c.get("function") or {}).get("name") or tool
    return {"first_s": first, "total_s": time.perf_counter() - t0, "tool": tool,
            "served": served, "cost": cost, "text": text.strip()[:60]}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--budget", type=float, default=0.10)
    args = ap.parse_args()
    headers = {"Authorization": f"Bearer {read_key()}", "X-Title": "me3-voice-agent latency check"}
    spent = 0.0
    rows: dict[str, list[dict[str, Any]]] = {m: [] for m in MODELS}
    with httpx.Client(headers=headers, timeout=30) as http:
        one(http, MODELS[1], "Hi.")  # warm the connection; not counted
        for rep in range(args.reps):
            for prompt, want in PROMPTS:
                for model in MODELS:
                    if spent >= args.budget * 0.9:
                        print(f"budget reached (${spent:.4f}); stopping")
                        return report(rows, spent)
                    r = one(http, model, prompt)
                    spent += float(r.get("cost") or 0)
                    r.update(prompt=prompt, want=want, rep=rep)
                    rows[model].append(r)
                    print(f"{model:<30} {prompt:<32} " + (r["error"] if "error" in r else
                          f"first {r['first_s'] or 0:.2f}s total {r['total_s']:.2f}s tool "
                          f"{r['tool']} served {r['served']} ${float(r['cost'] or 0):.5f}"),
                          flush=True)  # fmt: skip
    return report(rows, spent)


def report(rows: dict[str, list[dict[str, Any]]], spent: float) -> int:
    print(f"\ntotal spent: ${spent:.4f}")
    for model, rs in rows.items():
        ok = [r for r in rs if "error" not in r and r["first_s"] is not None]
        if not ok:
            print(f"{model}: no successful requests")
            continue
        first = sorted(r["first_s"] for r in ok)
        right = sum(r["tool"] == r["want"] for r in ok)
        served = sorted({str(r["served"]) for r in ok})
        print(f"{model}: n={len(ok)} first-token p50 {statistics.median(first):.2f}s "
              f"max {first[-1]:.2f}s; right tool {right}/{len(ok)}; served by {served}; "
              f"cost ${sum(float(r['cost'] or 0) for r in ok):.4f}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
