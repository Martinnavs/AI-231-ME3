"""Streaming vs blocking grounded Gemini (STREAM-RULE.md). Paid: 8 requests, cap $0.15. Key never printed."""
import asyncio, json, re, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "src")
import httpx
from voice_agent.mcp_servers import gemini_grounding as gg

QS = ["Who is the current Prime Minister of Japan?", "What is the current price of Brent crude oil?",
      "Who won the most recent Formula One race?", "What is the latest stable version of the Linux kernel?"]
CAP = 0.15; USD_IN, USD_OUT, USD_SEARCH = 0.25e-6, 1.50e-6, 0.014
STREAM_URL = gg.GEMINI_URL.replace(":generateContent", ":streamGenerateContent") + "?alt=sse"
SENT = re.compile(r"[.!?](\s|$)")

def body(q):
    today = f"{datetime.now(ZoneInfo(gg.TIMEZONE)):%A, %B %-d, %Y}"
    return {"systemInstruction": {"parts": [{"text": gg.SYSTEM.format(today=today)}]},
            "contents": [{"parts": [{"text": q}]}], "tools": [{"google_search": {}}],
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "minimal"}}}

def usage(u, searches):
    p = u.get("promptTokenCount", 0); return p * USD_IN + (u.get("totalTokenCount", 0) - p) * USD_OUT + searches * USD_SEARCH

async def blocking(http, key, q):
    t = time.perf_counter(); r = await http.post(gg.GEMINI_URL, json=body(q), headers={"x-goog-api-key": key}); end = time.perf_counter() - t
    data = r.json(); text, searches, _, _ = gg.parse(data)
    return {"mode": "block", "status": r.status_code, "first_sentence_s": end, "first_text_s": end, "end_s": end,
            "searches": searches, "text": text, "usd": usage(data.get("usageMetadata") or {}, searches)}

async def streaming(http, key, q):
    t = time.perf_counter(); text = ""; first = sent = None; queries = set(); u = {}
    async with http.stream("POST", STREAM_URL, json=body(q), headers={"x-goog-api-key": key}) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data:"): continue
            chunk = json.loads(line[5:])
            for c in chunk.get("candidates", []):
                for p in c.get("content", {}).get("parts", []):
                    if p.get("text") and not p.get("thought"):
                        text += p["text"]; first = first or time.perf_counter() - t
                queries.update((c.get("groundingMetadata") or {}).get("webSearchQueries") or [])
            u = chunk.get("usageMetadata") or u
            if sent is None and SENT.search(text): sent = time.perf_counter() - t
        status = r.status_code
    end = time.perf_counter() - t
    return {"mode": "stream", "status": status, "first_sentence_s": sent or end, "first_text_s": first, "end_s": end,
            "searches": len(queries), "text": text.strip(), "usd": usage(u, len(queries))}

async def main():
    key = gg.load_key(); assert key
    done = [json.loads(l) for l in open(".scratch/grounding-speed/stream_test.out")] if len(sys.argv) > 1 else []
    sent_before = {(d["q"], d["mode"]) for d in done} | {(QS[1], "block")}  # the call that hit a read error: not re-sent (no retries)
    spent = sum(d["usd"] for d in done) + (0.014 if done else 0.0)  # count the failed call at worst case
    out = open(".scratch/grounding-speed/stream_test.out", "a" if done else "w")
    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(keepalive_expiry=120)) as http:
        await http.get("https://generativelanguage.googleapis.com/", timeout=5)  # warm the connection (no model call)
        for i, q in enumerate(QS):
            for mode in (("block", "stream") if i % 2 == 0 else ("stream", "block")):
                if done and (q, mode) in sent_before: continue
                if spent >= CAP * 0.9: print("STOPPED", file=sys.stderr); return
                try:
                    r = await (blocking if mode == "block" else streaming)(http, key, q)
                except httpx.HTTPError as e:
                    out.write(json.dumps({"q": q, "mode": mode, "error": type(e).__name__}) + "\n"); spent += 0.014; continue
                spent += r["usd"]; r["q"] = q
                for k in ("first_sentence_s", "first_text_s", "end_s", "usd"): r[k] = round(r[k], 4) if r[k] is not None else None
                out.write(json.dumps(r) + "\n"); out.flush()
    print(f"spent ${spent:.4f}", file=sys.stderr)

if __name__ == "__main__":
    asyncio.run(main())
