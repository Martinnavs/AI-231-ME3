"""P0a tool-calling probe: can each model make well-formed tool calls, and what does it cost.

Three tiny scenarios per model, dummy tools, low reasoning effort, hard token and dollar caps.
Keys are read from files at run time and never printed; error text is scrubbed of the key.
Usage: uv run --offline python .scratch/onit-tools/probe/probe.py [model-key ...]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

from doc_chat.qwen import _read_secret_file, normalize_base_url

OUT = Path(__file__).with_name("results.jsonl")
HOME = Path.home() / ".secrets"
MAX_TOKENS = 1500
MODEL_CAP_USD = 0.03
POOL_CAP_USD = {"openrouter": 0.30, "gemini": 0.10, "qwen": 1.0}

TOOLS = [
    {"type": "function", "function": {"name": "get_weather", "description": "Current weather for a city.",
     "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}},
    {"type": "function", "function": {"name": "add", "description": "Add two numbers.",
     "parameters": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]
RESULTS = {"get_weather": "18C and cloudy", "add": "42", "read_file": "hello"}
SCENARIOS = [
    ("single", "What's the weather in Paris?", {"get_weather"}, "18"),
    ("multi", "What's the weather in Tokyo, and what is 17 plus 25? Use the tools.", {"get_weather", "add"}, "42"),
    ("none", "Say hello in one word.", set(), ""),
]
# key -> (pool, base url, model id, price in/out per M, extra body); prices are list prices (2026-10-29)
OR = "https://openrouter.ai/api/v1"
GEM = "https://generativelanguage.googleapis.com/v1beta/openai"
LOW_OR = {"reasoning": {"effort": "low"}, "usage": {"include": True}}
MODELS: dict[str, tuple[str, str, str, tuple[float, float], dict]] = {
    "gemini-3.8-flash": ("gemini", GEM, "gemini-3.8-flash", (0.75, 3.75), {"reasoning_effort": "low"}),
    "gemini-3.1-flash-lite": ("gemini", GEM, "gemini-3.1-flash-lite", (0.25, 1.5), {"reasoning_effort": "low"}),
    "haiku-5.5": ("openrouter", OR, "anthropic/claude-haiku-5.5", (0.1, 0.5), LOW_OR),
    "glm-5.3-flash": ("openrouter", OR, "z-ai/glm-5.3-flash", (0.05, 0.08), LOW_OR),
    "glm-5.3-flashx": ("openrouter", OR, "z-ai/glm-5.3-flashx", (0.37, 1.25), LOW_OR),
    "deepseek-v4.1-flash": ("openrouter", OR, "deepseek/deepseek-v4.1-flash", (0.045, 0.5), LOW_OR),
    "minimax-m3.1-flash": ("openrouter", OR, "minimax/minimax-m3.1-flash", (0.15, 0.6), LOW_OR),
    "qwen3.8-flash": ("openrouter", OR, "qwen/qwen3.8-flash", (0.15, 0.47), LOW_OR),
    "mimo-v2.6-flash": ("openrouter", OR, "xiaomi/mimo-v2.6-flash", (0.14, 0.28), LOW_OR),
    "class-qwen": ("qwen", "", "", (0.0, 0.0), {}),
}


def credentials(pool: str) -> tuple[str, str]:
    """(api key, base url override or '') for a pool; never printed."""
    if pool == "gemini":
        return (HOME / "google-ai-studio" / "api-key.txt").read_text().strip(), ""
    if pool == "openrouter":
        return (HOME / "openrouter" / "api-key.txt").read_text().strip(), ""
    key = _read_secret_file("api_key.txt") or ""
    address = _read_secret_file("host_address.txt") or ""
    return key, normalize_base_url(address)


def post(client: httpx.Client, url: str, key: str, body: dict) -> dict:
    reply = client.post(url + "/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
    if reply.status_code >= 400:
        text = reply.text.replace(key, "[key]")[:200]
        raise RuntimeError(f"HTTP {reply.status_code}: {text}")
    return reply.json()


def run_scenario(client, url, key, model, extra, prices, name, prompt, want, answer):
    messages = [{"role": "user", "content": prompt}]
    called: list[str] = []
    bad_json = 0
    tokens_in = tokens_out = 0
    cost = 0.0
    provider = ""
    started = time.monotonic()
    first_call_s = None
    final = ""
    for _ in range(5):
        body = {"model": model, "messages": messages, "tools": TOOLS, "max_tokens": MAX_TOKENS, **extra}
        data = post(client, url, key, body)
        provider = data.get("provider", provider)
        usage = data.get("usage") or {}
        tokens_in += usage.get("prompt_tokens", 0)
        tokens_out += usage.get("completion_tokens", 0)
        cost += usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else (
            usage.get("prompt_tokens", 0) * prices[0] + usage.get("completion_tokens", 0) * prices[1]) / 1e6
        message = data["choices"][0]["message"]
        messages.append({k: v for k, v in message.items() if v is not None})
        calls = message.get("tool_calls") or []
        if not calls:
            final = message.get("content") or ""
            break
        if first_call_s is None:
            first_call_s = time.monotonic() - started
        for call in calls:
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                bad_json += 1
                args = {}
            called.append(fn["name"])
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": RESULTS.get(fn["name"], "?")})
    ok_calls = set(called) == want and bad_json == 0 and (len(called) == len(want) or name == "single")
    ok_text = bool(final.strip()) and (answer in final if answer else True)
    return {
        "scenario": name, "pass": ok_calls and ok_text, "called": called, "bad_json": bad_json,
        "seconds": round(time.monotonic() - started, 2),
        "first_call_s": None if first_call_s is None else round(first_call_s, 2),
        "tokens_in": tokens_in, "tokens_out": tokens_out, "usd": round(cost, 5), "provider": provider,
        "final": final[:80],
    }


def probe(key_name: str, spent: dict[str, float]) -> None:
    pool, url, model, prices, extra = MODELS[key_name]
    key, base = credentials(pool)
    url = url or base
    with httpx.Client(timeout=90.0) as client:
        if pool == "qwen":
            ids = [m["id"] for m in client.get(url + "/models", headers={"Authorization": f"Bearer {key}"}).json()["data"]]
            print(f"  class endpoint serves: {ids}")
            model = ids[0] if ids else ""
        total = 0.0
        for name, prompt, want, answer in SCENARIOS:
            if spent[pool] >= POOL_CAP_USD[pool] or total >= MODEL_CAP_USD:
                print(f"  {name}: skipped (cap)")
                continue
            try:
                row = run_scenario(client, url, key, model, extra, prices, name, prompt, want, answer)
            except Exception as exc:
                if extra and "HTTP 400" in str(exc):  # a parameter we sent was refused: retry plain
                    row = run_scenario(client, url, key, model, {}, prices, name, prompt, want, answer)
                    row["note"] = "reasoning param refused"
                else:
                    row = {"scenario": name, "pass": False, "error": str(exc)[:160]}
            row.update(model=key_name, served=model)
            total += row.get("usd", 0.0)
            spent[pool] += row.get("usd", 0.0)
            OUT.open("a").write(json.dumps(row) + "\n")
            print(f"  {name}: {'PASS' if row['pass'] else 'FAIL'} {row.get('seconds', '-')}s ${row.get('usd', 0):.4f} "
                  f"{row.get('called', '')} {row.get('error', '')}")


def main() -> int:
    wanted = sys.argv[1:] or list(MODELS)
    spent = dict.fromkeys(POOL_CAP_USD, 0.0)
    for key_name in wanted:
        print(key_name)
        try:
            probe(key_name, spent)
        except Exception as exc:
            print(f"  failed: {type(exc).__name__}: {str(exc)[:120]}")
    print({k: round(v, 4) for k, v in spent.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
