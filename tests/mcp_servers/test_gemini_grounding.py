"""Gemini with Google Search first, then Tavily, then Wikipedia: order, spend ledger, prompt."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastmcp import Client

from voice_agent.mcp_servers import gemini_grounding, tavily_search
from voice_agent.mcp_servers.gemini_grounding import Ledger, parse
from voice_agent.mcp_servers.tavily_search import build_server

TAVILY = {"answer": "Tavily says Leonardo painted it.", "results": [{"content": "Mona Lisa"}]}
SEARCHED = {
    "candidates": [
        {
            "content": {
                "parts": [{"text": "**Isko Moreno** is the mayor.\nHe took office in 2025."}]
            },
            "groundingMetadata": {
                "webSearchQueries": ["mayor of manila"],
                "groundingChunks": [{"web": {"title": "Manila - Wikipedia"}}],
            },
        }
    ],
    "usageMetadata": {"totalTokenCount": 83},
}
SETTLED = {
    "candidates": [{"content": {"parts": [{"text": "Canberra is the capital of Australia."}]}}],
    "usageMetadata": {"totalTokenCount": 54},
}


class Both(httpx.AsyncBaseTransport):
    """Gemini and Tavily endpoints with scripted replies; records the requests."""

    def __init__(self, gemini: httpx.Response | Exception, delay: float = 0.0) -> None:
        self.gemini, self.delay = gemini, delay
        self.seen: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = str(request.url)
        if url.startswith(gemini_grounding.GEMINI_URL):
            await asyncio.sleep(self.delay)
            if isinstance(self.gemini, Exception):
                raise self.gemini
            return self.gemini
        if url.startswith(tavily_search.TAVILY_URL):
            return httpx.Response(200, json=TAVILY)
        return httpx.Response(404)

    def count(self, prefix: str) -> int:
        return len([r for r in self.seen if str(r.url).startswith(prefix)])


async def _ask(
    transport: Both,
    query: str = "Who is the mayor of Manila?",
    gemini_key: str | None = "fake-gemini",
    ledger: Ledger | None = None,
    key: str | None = "fake-tavily",
) -> str:
    server = build_server(
        transport, key, None, grounding=True, gemini_key=gemini_key,
        ledger=ledger or Ledger(None),
    )  # fmt: skip
    async with Client(server) as client:
        result = await client.call_tool("web_answer", {"query": query})
    return str(result.structured_content["result"])


def test_parse_takes_the_text_the_searches_and_the_page_titles() -> None:
    text, searches, titles, tokens = parse(SEARCHED)
    assert text == "Isko Moreno is the mayor. He took office in 2025."  # no markdown, one line
    assert (searches, titles, tokens) == (1, ["Manila - Wikipedia"], 83)
    assert parse(SETTLED)[1] == 0 and parse({})[0] == ""


async def test_gemini_answers_first_and_tavily_is_not_asked() -> None:
    transport = Both(httpx.Response(200, json=SEARCHED))
    text = await _ask(transport)
    assert text == "Isko Moreno is the mayor. He took office in 2025."
    assert transport.count(tavily_search.TAVILY_URL) == 0


async def test_the_request_has_the_search_tool_todays_date_and_the_key_as_a_header() -> None:
    transport = Both(httpx.Response(200, json=SETTLED))
    await _ask(transport, "What is the capital of Australia?")
    request = transport.seen[0]
    body = json.loads(request.content)
    assert request.headers["x-goog-api-key"] == "fake-gemini" and "key=" not in str(request.url)
    assert body["tools"] == [{"google_search": {}}]
    today = f"{datetime.now(ZoneInfo(gemini_grounding.TIMEZONE)):%A, %B %-d, %Y}"
    system = body["systemInstruction"]["parts"][0]["text"]
    assert today in system and "Search the web" in system
    assert body["contents"][0]["parts"][0]["text"] == "What is the capital of Australia?"


@pytest.mark.parametrize(
    "gemini",
    [httpx.Response(429), httpx.Response(200, json={"candidates": []}),
     httpx.Response(200, text="not json"), httpx.ConnectError("down")],
)  # fmt: skip
async def test_tavily_answers_when_gemini_does_not(gemini: httpx.Response | Exception) -> None:
    transport = Both(gemini)
    assert await _ask(transport, "Who painted the Mona Lisa?") == TAVILY["answer"]
    assert transport.count(gemini_grounding.GEMINI_URL) == 1


async def test_a_slow_gemini_is_dropped_for_tavily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tavily_search, "GEMINI_TIMEOUT_S", 0.05)
    transport = Both(httpx.Response(200, json=SETTLED), delay=0.5)
    assert await _ask(transport, "Who painted the Mona Lisa?") == TAVILY["answer"]


async def test_without_a_gemini_key_tavily_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_grounding, "KEY_FILE", Path("/nonexistent/key"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    transport = Both(httpx.Response(200, json=SETTLED))
    assert await _ask(transport, "Who painted the Mona Lisa?", gemini_key=None) == TAVILY["answer"]
    assert transport.count(gemini_grounding.GEMINI_URL) == 0


async def test_geminis_answer_is_not_second_guessed_by_the_name_check() -> None:
    reply = {"candidates": [{"content": {"parts": [{"text": "That year has not happened yet."}]}}]}
    transport = Both(httpx.Response(200, json=reply))
    text = await _ask(transport, "Who won the 2087 Nobel Prize in Physics?")
    assert text == "That year has not happened yet."


async def test_every_call_is_in_the_ledger_and_the_cap_sends_the_next_to_tavily(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, cap=1)
    transport = Both(httpx.Response(200, json=SEARCHED))
    assert "Isko Moreno" in await _ask(transport, "Who is the mayor of Manila?", ledger=ledger)
    row = json.loads(path.read_text())
    assert (row["searches"], row["tokens"]) == (1, 83) and "query" not in row  # no question text
    assert ledger.exhausted
    # the cap is reached: a different question goes straight to Tavily
    assert await _ask(transport, "Who painted the Mona Lisa?", ledger=ledger) == TAVILY["answer"]
    assert transport.count(gemini_grounding.GEMINI_URL) == 1


def test_the_ledger_counts_this_month_only_and_survives_a_bad_file(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        '{"month": "2026-10", "searches": 40}\n{"month": "2026-11", "searches": 7}\nnot json\n'
    )
    assert Ledger(path, month="2026-11").searches == 0  # a bad line: start from zero, not crash
    path.write_text('{"month": "2026-10", "searches": 40}\n{"month": "2026-11", "searches": 7}\n')
    assert Ledger(path, month="2026-11").searches == 7
    assert Ledger(tmp_path / "missing.jsonl").searches == 0
    unwritable = Ledger(tmp_path / "no" / "such" / "dir" / "x.jsonl")
    unwritable.path = tmp_path / "file" / "x.jsonl"
    (tmp_path / "file").write_text("a file, not a folder")
    unwritable.add(gemini_grounding.Grounded("t", 2, [], None, 0.1))  # must not raise
    assert unwritable.searches == 2


async def test_a_failed_grounded_call_is_a_costless_ledger_row(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, month="2026-11")
    await _ask(Both(httpx.ConnectError("down")), ledger=ledger)
    row = json.loads(path.read_text().splitlines()[0])
    assert row["failed"] == "ConnectError" and row["searches"] == 0 and row["tokens"] == 0
    assert Ledger(path, month="2026-11").searches == 0


async def test_a_grounded_timeout_is_logged_as_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(tavily_search, "GEMINI_TIMEOUT_S", 0.05)
    await _ask(Both(httpx.Response(200), delay=1.0), ledger=Ledger(path, month="2026-11"))
    assert json.loads(path.read_text().splitlines()[0])["failed"] == "timeout"
