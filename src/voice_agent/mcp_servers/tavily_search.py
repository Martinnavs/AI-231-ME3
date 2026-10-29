"""Tavily web search MCP server (fastmcp): one short, sayable answer to a factual question.

uv run --group laptop python -m voice_agent.mcp_servers.tavily_search

Prototype that replaces the Wikipedia lookup for general-knowledge questions: Tavily's search API
(https://api.tavily.com/search) writes a short answer from live web results, so it covers people,
places, current facts and news, not only subjects with an encyclopedia page. Wikipedia stays as
the fallback when the key is missing or Tavily does not answer (no key, HTTP error, timeout, no
answer), so a lookup never gets worse than before.

The API key is read at run time from $TAVILY_API_KEY or ~/.secrets/tavily/api-key.txt
($TAVILY_KEY_FILE overrides the path). It is sent only as the Authorization header and is never
logged or put in a result. The question text is sent to Tavily.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

from voice_agent.mcp_servers.web_info import USER_AGENT, first_sentences, wiki_text

TAVILY_URL = "https://api.tavily.com/search"
KEY_FILE = Path(os.environ.get("TAVILY_KEY_FILE", Path.home() / ".secrets/tavily/api-key.txt"))
TIMEOUT_S = 4.0  # per phase of one HTTP request
TOTAL_TIMEOUT_S = 3.5  # the whole Tavily call; slower than this and Wikipedia answers instead
# basic took 2.35 s median on fresh queries from Manila, fast 1.21 s, ultra-fast 1.24 s (2026-10-29,
# 4 queries each; a repeated query is cached by Tavily at about 0.25 s)
SEARCH_DEPTH = os.environ.get("TAVILY_DEPTH", "fast")  # basic | fast | ultra-fast | advanced
# Connection reuse (2026-10-29, Manila -> a US-East address, 0.25 s round trip): TCP + TLS + first
# byte cost 0.5-0.9 s on a fresh connection. httpx drops idle connections after 5 s by default, so
# every lookup paid it (a call after a 15 s pause: 1.93 s median; kept alive: 1.32 s). Tavily's
# server drops an idle connection somewhere between 15 and 45 s (a kept-alive call after 45 s idle
# took 3.5 s), so a cheap ping of the API root (no search is run) goes out while the server is used.
KEEPALIVE_S = 120.0
PING_URL = "https://api.tavily.com/"
PING_EVERY_S = 20.0
PING_WHILE_ACTIVE_S = 600.0  # stop pinging after this long without a lookup
CACHE_TTL_S = 600.0  # a repeated or rephrased-the-same question is answered from memory
CACHE_MAX = 64
LEAD_IN = re.compile(
    r"^(?:according to|based on)\s+(?:the\s+)?(?:provided\s+|given\s+|available\s+)?"
    r"(?:sources?|search results?|information|text|context)[^,]*,\s*",
    re.IGNORECASE,
)
MAX_RESULTS = 3
ANSWER_SENTENCES = 2
ANSWER_CHARS = 400


def load_key() -> str | None:
    """The API key from the environment or the key file, or None (never printed or logged)."""
    key = os.environ.get("TAVILY_API_KEY")
    if key and key.strip():
        return key.strip()
    try:
        return KEY_FILE.read_text().strip() or None
    except OSError:
        return None


def spoken(data: dict[str, Any]) -> str | None:
    """Tavily's answer as one or two sentences to say, else the best result's snippet, else None."""
    answer = LEAD_IN.sub("", str(data.get("answer") or "").strip())
    if answer:
        answer = answer[0].upper() + answer[1:]  # "According to the sources, x is y." -> "X is y."
        return first_sentences(answer, ANSWER_SENTENCES, ANSWER_CHARS)
    for result in data.get("results") or []:
        snippet = str(result.get("content") or "").strip()
        if snippet:
            return first_sentences(snippet, ANSWER_SENTENCES, ANSWER_CHARS)
    return None


def cache_key(query: str) -> str:
    """The question without case, punctuation or extra spaces: 'Who won?' and 'who won' match."""
    return " ".join(re.findall(r"[a-z0-9']+", query.lower()))


def build_server(
    transport: httpx.AsyncBaseTransport | None = None,
    key: str | None = None,
    ping_every_s: float | None = PING_EVERY_S,
    clock: Callable[[], float] = time.monotonic,
) -> FastMCP:
    """A FastMCP server with web_answer; `transport`, `key`, `ping_every_s` (None: no pings) and
    `clock` are for tests (key None: load it)."""
    clients: list[httpx.AsyncClient] = []  # made on first use, inside the server's event loop
    cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
    last_use = [clock()]

    def client() -> httpx.AsyncClient:
        if not clients:
            clients.append(
                httpx.AsyncClient(
                    transport=transport,
                    timeout=TIMEOUT_S,
                    headers={"User-Agent": USER_AGENT},
                    limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S),
                )
            )
        return clients[0]

    async def keep_warm(every: float) -> None:
        """Open the connection now, then touch it while lookups are happening."""
        while True:
            if clock() - last_use[0] < PING_WHILE_ACTIVE_S:
                with contextlib.suppress(httpx.HTTPError):
                    await client().get(PING_URL, timeout=5.0)
            await asyncio.sleep(every)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        task = asyncio.create_task(keep_warm(ping_every_s)) if ping_every_s else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            for c in clients:
                await c.aclose()

    server = FastMCP("tavily_search", lifespan=lifespan)

    @server.tool(
        description=(
            "Look up a factual question on the web and get a short answer to say: people, "
            "places, books, films, facts, current events, how things work. Write `query` as a "
            "complete question, for example 'Who wrote Blood Meridian?'. Say the answer as it is."
        )
    )
    async def web_answer(query: str) -> str:
        api_key = key or load_key()
        http = client()
        last_use[0] = clock()
        wanted = cache_key(query)
        hit = cache.get(wanted)
        if hit is not None and clock() - hit[0] < CACHE_TTL_S:
            cache.move_to_end(wanted)
            return hit[1]
        if api_key:
            body = {
                "query": query,
                "search_depth": SEARCH_DEPTH,
                "include_answer": "basic",
                "max_results": MAX_RESULTS,
            }
            try:
                async with asyncio.timeout(TOTAL_TIMEOUT_S):
                    response = await http.post(
                        TAVILY_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}
                    )
                response.raise_for_status()
                answer = spoken(response.json())
                if answer:
                    cache[wanted] = (clock(), answer)
                    while len(cache) > CACHE_MAX:
                        cache.popitem(last=False)
                    return answer
            except (httpx.HTTPError, ValueError, TimeoutError):
                pass  # fall through to Wikipedia
        return await wiki_text(http, query)

    return server


def main(argv: Sequence[str] | None = None) -> int:
    build_server().run(show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
