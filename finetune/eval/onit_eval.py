"""Run the OnIt evaluation set against a backend and score it.

    cd pipeline && uv run python ../finetune/eval/onit_eval.py gemini [--name N]
    python3 onit_eval.py router-split --url http://127.0.0.1:8089 [--name N]   # local model, the split design
    python3 onit_eval.py router-full  --url http://127.0.0.1:8089 [--name N]   # local model, tools in its own language

gemini        hosted Gemini (the model the agent uses) with the 18 tool schemas, native function calling. Also times
              the second request (the spoken answer from a canned tool result) for the turns that need one.
router-split  the small model as a ROUTER: it answers home/info calls itself and must `escalate` everything else.
              Scored against the DEFERRAL expectation (docs/files/shell/serve/web/workflow/code -> any escalate).
router-ft     the fine-tuned router (its own short system message from data/format.py); scored like router-split.
router-full   the small model asked to make the OnIt tool calls itself (all tools in its prompt). Scored against the
              ORIGINAL call expectation: would its own call have been right?
Scoring (onit_prompts.py): a turn is exact when its calls match one acceptable alternative (names and argument
checks); name_ok when only the names match. Gemini is not scored on workflow/code prompts (it has no "escalate"
and simply starts the work); the router is. Latency: seconds from sending the request to the first call/text.
"""

import argparse
import json
import re
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # the repository root
sys.path.insert(0, str(HERE.parent / "spec"))
import tools as T  # noqa: E402

# single current-fact web items: the router may pass the question to web_answer instead of deferring it
WEB_ANSWER_OK = {"w01", "w02", "w04"}
DEFER_KINDS = {"docs", "files", "shell", "serve", "web", "workflow", "code"}
RESULT_KINDS = {
    "docs",
    "files",
    "shell",
    "serve",
    "web",
}  # a second request writes the spoken answer
CANNED = {
    "local_search": "policies/billing.pdf section 4.2: Annual plans are refundable pro rata within the first 60 days only. policies/faq.md: Monthly plans have no refund window.",
    "read_file": "# Project notes\nThe service runs on port 8080. Deploys happen on Fridays. Contact the platform team for access. See docs/setup.md for the local setup steps.",
    "grep": "src/app.py:12: # TODO handle timeouts\nsrc/db.py:40: # TODO close the connection\nsrc/utils.py:7: # TODO remove after migration",
    "bash": "LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 511 0.0.0.0:6379 0.0.0.0:*\nLISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*",
    "serve": "job webapp started, pid 4321, logs in .onit/jobs/webapp.log",
    "web_search": "1. Python 3.14 released - python.org: the new release adds ... 2. What's new in 3.14 - docs.python.org ... 3. Release schedule - peps.python.org ...",
    "fetch_content": "Example Domain. This domain is for use in illustrative examples in documents. You may use this domain without prior coordination.",
}


# ---------------------------------------------------------------------------------------------- scoring
def arg_ok(check, value: str) -> bool:
    if check is None:
        return True
    value = (value or "").strip()
    if isinstance(check, str):
        return value.lower() == check.lower()
    kind, arg = check
    if kind == "re":
        return re.search(arg, value) is not None
    if kind == "has":
        return all(w.lower() in value.lower() for w in arg)
    if kind == "in":
        return value.lower() in [a.lower() for a in arg]
    raise ValueError(check)


def match_alt(calls, alt, names_only=False) -> bool:
    if len(calls) != len(alt):
        return False
    pool = list(calls)
    for name, checks in alt:
        for i, (cname, cargs) in enumerate(pool):
            if cname == name and (
                names_only or all(arg_ok(c, cargs[j] if j < len(cargs) else "") for j, c in enumerate(checks))
            ):
                pool.pop(i)
                break
        else:
            return False
    return True


def _swap_docs(original):
    """search_docs is a summarising tool, so the split router defers it."""
    return [[("escalate", [None]) if c[0] == "search_docs" else c for c in alt] for alt in original]


def defer_expect(kind, original, pid=None):
    """The deferral expectation of the split design: every request for a summarising tool, code or a workflow
    must produce an escalate call; other kinds keep their expectation."""
    if kind.startswith("pack-"):
        swapped = _swap_docs(original)
        if [] in swapped:  # "no tool expected": a spoken reply (`none|text`) is no tool call either
            swapped.append([("none", [None])])
        return swapped
    if kind == "web" and pid in WEB_ANSWER_OK:
        return [[("escalate", [None])], [("web_answer", [None])]]
    if kind in DEFER_KINDS:
        return [[("escalate", [None])]]
    if kind == "multi":
        return [[("escalate", [None])]]
    if kind == "safety":  # nothing may run: a spoken refusal or any deferral to the hosted model (which refuses)
        return [[], [("escalate", [None])], [("none", [None])]]
    return original


def pack_prompts():
    """The 87-prompt pack (the agent's current tools) in this file's format, so one runner checks both."""
    out = []
    for line in (ROOT / "pipeline/config/eval/agent_prompts.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        per_turn = []
        for turn_expect in p["expect"]:
            call_list = []
            for e in turn_expect:
                if "tool" in e:
                    call_list.append((e["tool"], [None]))
                else:
                    slot = e.get("slot")
                    check = (
                        ("re", r"^$")
                        if not slot
                        else (("has", slot.lower().split()) if e["intent"] == "CREATE_REMINDER" else slot)
                    )
                    call_list.append((e["intent"], [check]))
            per_turn.append([call_list])
        out.append({"id": p["id"], "kind": "pack-" + p["kind"], "turns": p["turns"], "expect": per_turn})
    return out


# ---------------------------------------------------------------------------------------------- backends
def run_local(url, system, grammar, messages) -> dict:
    body = {
        "messages": [{"role": "system", "content": system}, *messages],
        "grammar": grammar,
        "max_tokens": 48,
        "temperature": 0,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    start = time.perf_counter()
    req = urllib.request.Request(
        url + "/v1/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=120))
    wall = time.perf_counter() - start
    line = (data["choices"][0]["message"].get("content") or "").strip()
    try:
        calls = [(c.name, c.args) for c in T.parse_output(line)]
        err = None
    except T.LineError as exc:
        calls, err = [], str(exc)
    return {
        "calls": calls,
        "text": line,
        "latency_s": wall,
        "error": err,
        "timings": data.get("timings", {}),
    }


class Gemini:
    def __init__(self, system: str):
        from google import genai
        from google.genai import types
        from voice_pipeline.agent import MODEL, read_key, warm_http_options

        self.types, self.model = types, MODEL
        self.client = genai.Client(api_key=read_key("google-ai-studio"), http_options=warm_http_options())
        decls = []
        for t in T.openai_tools():
            f = t["function"]
            decls.append(
                types.FunctionDeclaration(
                    name=f["name"],
                    description=f["description"],
                    parameters_json_schema=f["parameters"],
                )
            )
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=decls)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0,
        )

    async def stream(self, contents):
        start = time.perf_counter()
        first, calls, text, fparts = None, [], "", []
        async for chunk in await self.client.aio.models.generate_content_stream(
            model=self.model, contents=contents, config=self.config
        ):
            for cand in chunk.candidates or []:
                for part in (cand.content.parts if cand.content else None) or []:
                    if first is None:
                        first = time.perf_counter() - start
                    if part.function_call:
                        fparts.append(part)  # keeps the thought_signature Gemini 3 requires when the call is replayed
                        calls.append((part.function_call.name, dict(part.function_call.args or {})))
                    elif part.text:
                        text += part.text
        self.last_parts = fparts
        return calls, text, first or (time.perf_counter() - start)

    def positional(self, name, args):
        tool = T.BY_NAME.get(name)
        if tool is None:  # smart_home
            return [str(args.get("intent", "")), str(args.get("slot", "") or "")]
        return [str(args.get(a.name, "") if args.get(a.name) is not None else "") for a in tool.args]

    async def turn(self, history, user, kind):
        types = self.types
        contents = history + [types.Content(role="user", parts=[types.Part(text=user)])]
        calls, text, first = await self.stream(contents)
        pos = [
            ((a.get("intent", n), [str(a.get("slot", "") or "")]) if n == "smart_home" else (n, self.positional(n, a)))
            for n, a in calls
        ]
        out = {"calls": pos, "text": text, "latency_s": first, "error": None, "leg2_s": None}
        new_history = contents
        if calls:
            fc = types.Content(role="model", parts=list(self.last_parts))
            fr = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(name=n, response={"result": CANNED.get(n, "ok")})
                    )
                    for n, _ in calls
                ],
            )
            if kind in RESULT_KINDS:
                saved = self.last_parts
                _, _, leg2 = await self.stream(contents + [fc, fr])
                out["leg2_s"] = leg2
                self.last_parts = saved
            new_history = contents + [
                fc,
                fr,
                types.Content(role="model", parts=[types.Part(text="Done.")]),
            ]
        else:
            new_history = contents + [types.Content(role="model", parts=[types.Part(text=text or "Okay.")])]
        return out, new_history


# ---------------------------------------------------------------------------------------------- main
def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))] if xs else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("backend", choices=("gemini", "router-split", "router-full", "router-ft"))
    ap.add_argument("--url", default="http://127.0.0.1:8089")
    ap.add_argument("--name", default=None)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument(
        "--set",
        choices=("onit", "pack"),
        default="onit",
        help="the OnIt set, or the 87-prompt regression pack",
    )
    a = ap.parse_args()
    name = a.name or a.backend
    out_dir = HERE / "results" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts = (
        pack_prompts()
        if a.set == "pack"
        else [json.loads(x) for x in (HERE / "onit_eval.jsonl").read_text().splitlines() if x.strip()]
    )
    rows = []

    if a.backend == "gemini":
        import asyncio

        base = (ROOT / "pipeline/config/eval/system_agent.txt").read_text()
        system_g = base + "\n" + (HERE / "system_gemini_onit.txt").read_text()

        async def go():
            g = Gemini(system_g)  # built inside the loop: the aiohttp session belongs to it
            for rep in range(a.repeat):
                for p in prompts:
                    history = []
                    for i, (turn, expect) in enumerate(zip(p["turns"], p["expect"], strict=True)):
                        r, history = await g.turn(history, turn, p["kind"])
                        rows.append(
                            {
                                **r,
                                "id": p["id"],
                                "kind": p["kind"],
                                "turn": i,
                                "rep": rep,
                                "expect": expect,
                            }
                        )
            await __import__("voice_pipeline.agent", fromlist=["close_llm_sessions"]).close_llm_sessions()

        asyncio.run(go())
    else:
        system = (
            HERE / ("system_router_split.txt" if a.backend == "router-split" else "system_router_full.txt")
        ).read_text()
        grammar = T.grammar()
        run_local(a.url, system, grammar, [{"role": "user", "content": "Say hi."}])  # warm the prompt cache
        for rep in range(a.repeat):
            for p in prompts:
                messages = []
                for i, (turn, expect) in enumerate(zip(p["turns"], p["expect"], strict=True)):
                    messages.append({"role": "user", "content": turn})
                    r = run_local(a.url, system, grammar, messages)
                    messages.append({"role": "assistant", "content": r["text"] or "none|Okay."})
                    rows.append(
                        {
                            **r,
                            "id": p["id"],
                            "kind": p["kind"],
                            "turn": i,
                            "rep": rep,
                            "expect": expect,
                        }
                    )

    for r in rows:
        exp = r["expect"]
        if a.backend in ("router-split", "router-ft"):
            exp = defer_expect(r["kind"], exp, r["id"])
        r["scored"] = not (a.backend == "gemini" and r["kind"] in ("workflow", "code"))
        r["exact"] = any(match_alt(r["calls"], alt) for alt in exp)
        r["name_ok"] = any(match_alt(r["calls"], alt, names_only=True) for alt in exp)
        r["escalated"] = any(c[0] == "escalate" for c in r["calls"])
    with (out_dir / "rows.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps({k: v for k, v in r.items() if k != "timings"}, ensure_ascii=False) + "\n")

    groups = {"ALL": rows}
    for r in rows:
        groups.setdefault(r["kind"], []).append(r)
    print(f"{a.backend}: {len(rows)} turns")
    print(f"{'kind':<10}{'n':>4}{'exact':>7}{'name_ok':>9}{'lat p50':>9}{'lat p95':>9}{'leg2 p50':>10}{'leg2 p95':>10}")
    for k, g in sorted(groups.items()):
        sc = [r for r in g if r["scored"]]
        lat = [r["latency_s"] for r in g]
        l2 = [r["leg2_s"] for r in g if r.get("leg2_s")]
        ex = f"{sum(r['exact'] for r in sc)}/{len(sc)}" if sc else "-"
        nm = f"{sum(r['name_ok'] for r in sc)}/{len(sc)}" if sc else "-"
        print(
            f"{k:<10}{len(g):>4}{ex:>7}{nm:>9}{st.median(lat):>9.2f}{pct(lat, 0.95):>9.2f}"
            f"{(st.median(l2) if l2 else float('nan')):>10.2f}{pct(l2, 0.95) if l2 else float('nan'):>10.2f}"
        )
    used = sorted({r["id"] for r in rows if any(c[0] == "web_answer" for c in r["calls"])})
    web = [r for r in rows if r["kind"] == "web" and r["id"] in WEB_ANSWER_OK]
    print(
        f"web_answer used on {len(used)} item(s): {used}; "
        f"single-fact web items sent to it: {sum(any(c[0] == 'web_answer' for c in r['calls']) for r in web)}/{len(web)}"
    )


if __name__ == "__main__":
    main()
