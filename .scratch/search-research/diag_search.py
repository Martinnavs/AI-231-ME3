"""Why does the pipeline's request rarely search while the short REST request always does? Replay the real
config with one factor removed at a time. 16 calls, ledgered. Keys never printed."""
import asyncio, json, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, "src")
from google.genai import types
from voice_pipeline.agent import build_llm
from voice_pipeline.agent_tools import build_tools, standard_servers, with_builtin_search
from voice_pipeline.search_count import SearchCounter, queries_in

QS = ["What is the current price of crude oil?", "What is the latest version of iOS?",
      "Who won the men's singles title at the 2026 French Open?", "Who is the current Speaker of the House of Representatives of the Philippines?"]
SHORT = ("You are a voice assistant. Today is {today}. Your answer is spoken aloud: at most two short sentences, plain words, no lists, no markdown. "
         "Search the web before answering whenever the answer could have changed since your training: office holders, sports results, prices and rates, "
         "records, rankings, events described as latest, last, next, current or new. Do not search for settled facts.")
async def main():
    today = f"{datetime.now(ZoneInfo('Asia/Manila')):%A, %B %-d, %Y}"
    full = Path("config/eval/system_agent_search.txt").read_text().replace("{today}", today)
    short = SHORT.format(today=today)
    servers, optional = standard_servers(None)
    tools = await build_tools([], servers=servers, optional=optional)
    llm = build_llm(full, builtin_search=True)
    counter = SearchCounter(Path("out/agent_dryrun/diag-ledger.jsonl")); counter.attach(llm)
    provider = llm.get_llm_adapter().to_provider_tools_format(with_builtin_search(tools))
    decls = [d for t in provider for d in t.get("function_declarations", [])]
    two = [d for d in decls if d["name"] in ("weather", "smart_home")]
    print(f"function declarations in the pipeline request: {len(decls)} (names e.g. {[d['name'] for d in decls][:6]}); system prompt {len(full)} chars")
    variants = {
        "V0 pipeline as is":               (full,  [{"function_declarations": decls}, {"google_search": {}}]),
        "V1 short prompt, all tools":      (short, [{"function_declarations": decls}, {"google_search": {}}]),
        "V2 full prompt, 2 tools":         (full,  [{"function_declarations": two},   {"google_search": {}}]),
        "V3 short prompt, 2 tools (REST)": (short, [{"function_declarations": two},   {"google_search": {}}]),
    }
    models = llm._client.aio.models
    for name, (system, tl) in variants.items():
        hits = 0; t0 = []
        for q in QS:
            cfg = types.GenerateContentConfig(system_instruction=system, tools=tl, tool_config=types.ToolConfig(include_server_side_tool_invocations=True),
                                              thinking_config=types.ThinkingConfig(thinking_level="minimal"))
            t = time.perf_counter()
            stream = await models.generate_content_stream(model="gemini-3.1-flash-lite", contents=q, config=cfg)
            n = 0
            async for chunk in stream: n = max(n, queries_in(chunk))
            t0.append(time.perf_counter() - t); hits += 1 if n else 0
        print(f"{name:34} searched {hits}/{len(QS)}   median {sorted(t0)[len(t0)//2]:.2f}s")
    print("searches counted:", counter.searches)
asyncio.run(main())
