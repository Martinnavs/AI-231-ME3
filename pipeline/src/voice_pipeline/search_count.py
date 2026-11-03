"""Count the Google searches Gemini runs inside the agent's own requests, and enforce a cap.

With `--builtin-search` the model searches the web inside its request (agent_tools.
with_builtin_search). Google bills each search query, and one request can run several (the NBA
question ran 2), but Pipecat's Gemini service throws the query list away and only emits one
`LLMSearchResponseFrame` per grounded request. So the SDK's streaming call is wrapped: every
chunk's `grounding_metadata.web_search_queries` is read (the list grows as the response streams,
so only the increase is added) as the chunk passes, and the count goes to the same JSON-lines
ledger the `web_answer` MCP server writes (voice_agent/mcp_servers/gemini_grounding.py: one row
per call with `month` and `searches`), so both paths count against one monthly cap. At the cap the
`google_search` tool is taken out of the request and the model answers without searching.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

USD_PER_SEARCH = 0.014  # Google's price beyond the 5,000 free a month: the worst case
MONTHLY_CAP = 4500  # of the 5,000 free; the web_answer server uses the same figure
LEDGER = Path(
    os.environ.get(
        "WEB_ANSWER_LEDGER", Path.home() / ".local/state/voice_agent/web_answer_ledger.jsonl"
    )
)


def queries_in(chunk: Any) -> int:
    """Search queries the response has reported so far (0 when it did not search)."""
    for candidate in getattr(chunk, "candidates", None) or []:
        metadata = getattr(candidate, "grounding_metadata", None)
        if metadata is not None and metadata.web_search_queries:
            return len(metadata.web_search_queries)
    return 0


def tool_tokens_in(chunk: Any) -> int:
    """Prompt tokens the search results added (billed as input; usage reports them apart)."""
    usage = getattr(chunk, "usage_metadata", None)
    return int(getattr(usage, "tool_use_prompt_token_count", 0) or 0)


class SearchCounter:
    """Searches since this object was made, and this month's total from the shared ledger."""

    def __init__(
        self,
        ledger: Path | None = LEDGER,
        cap: int = MONTHLY_CAP,
        month: str = "",
    ) -> None:
        self.ledger, self.cap = ledger, cap
        self.month = month or time.strftime("%Y-%m")
        self.searches = 0  # since this object was made
        self.requests = 0  # grounded requests (ones that searched)
        self.tool_tokens = 0
        self.month_searches = self._read()

    def _read(self) -> int:
        if self.ledger is None:
            return 0
        try:
            rows = [json.loads(x) for x in self.ledger.read_text().splitlines() if x.strip()]
        except (OSError, ValueError):
            return 0
        return sum(int(r.get("searches", 0)) for r in rows if str(r.get("month")) == self.month)

    @property
    def exhausted(self) -> bool:
        return self.month_searches >= self.cap

    def record(
        self, searches: int, tool_tokens: int, seconds: float, first_of_request: bool = True
    ) -> None:
        """Add searches (and the tokens their results cost) to the totals and the ledger."""
        self.tool_tokens += tool_tokens
        if not searches:
            return
        self.searches += searches
        self.requests += first_of_request
        self.month_searches += searches
        if self.ledger is None:
            return
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "month": self.month,
               "searches": searches, "tokens": tool_tokens or None,
               "seconds": round(seconds, 2), "source": "builtin"}  # fmt: skip
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a") as sink:
                sink.write(json.dumps(row) + "\n")
        except OSError:
            pass  # a ledger that cannot be written must not lose the answer

    @staticmethod
    def without_search(config: Any) -> Any:
        """`config` (a GenerateContentConfig) with the google_search tool, and the option that
        only that tool needs, taken out."""
        tools = [t for t in (getattr(config, "tools", None) or []) if not t.google_search]
        return config.model_copy(update={"tools": tools or None, "tool_config": None})

    async def watch(self, stream: AsyncIterator[Any]) -> AsyncIterator[Any]:
        """Pass the chunks through, counting a search the moment a chunk reports it. The count is
        not left to the end of the stream: the report comes in the last chunk, and Pipecat may
        stop reading right after it, so code after the loop would never run."""
        started = time.perf_counter()
        searches = tokens = 0
        async for chunk in stream:
            found, used = queries_in(chunk), tool_tokens_in(chunk)
            if found > searches or used > tokens:
                self.record(
                    max(found, searches) - searches,
                    max(used, tokens) - tokens,
                    time.perf_counter() - started,
                    first_of_request=searches == 0,
                )
                searches, tokens = max(found, searches), max(used, tokens)
            yield chunk

    def attach(self, llm: Any) -> None:
        """Count (and cap) the searches of every request `llm` (a GoogleLLMService) makes."""
        models = llm._client.aio.models
        original = models.generate_content_stream

        async def counted(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            if self.exhausted and kwargs.get("config") is not None:
                kwargs["config"] = self.without_search(kwargs["config"])
            return self.watch(await original(*args, **kwargs))

        models.generate_content_stream = counted
