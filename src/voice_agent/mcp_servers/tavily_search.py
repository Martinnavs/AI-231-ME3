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
import os
import re
from collections.abc import Sequence
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


def build_server(
    transport: httpx.AsyncBaseTransport | None = None,
    key: str | None = None,
) -> FastMCP:
    """A FastMCP server with web_answer; `transport` and `key` are for tests (key None: load it)."""
    server = FastMCP("tavily_search")
    clients: list[httpx.AsyncClient] = []  # made on first use, inside the server's event loop

    def client() -> httpx.AsyncClient:
        if not clients:
            clients.append(
                httpx.AsyncClient(
                    transport=transport, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}
                )
            )
        return clients[0]

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
