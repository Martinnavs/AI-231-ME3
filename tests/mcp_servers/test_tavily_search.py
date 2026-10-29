import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from voice_agent.mcp_servers import tavily_search
from voice_agent.mcp_servers.tavily_search import build_server, load_key, spoken

WIKI_SEARCH = {"pages": [{"key": "Blood_Meridian", "title": "Blood Meridian"}]}
WIKI_SUMMARY = {
    "extract": "Blood Meridian is a 1985 novel by Cormac McCarthy. It is set in the West."
}
ANSWER = {
    "answer": "Cormac McCarthy wrote Blood Meridian. It was published in 1985. More text follows.",
    "results": [{"content": "A snippet.", "url": "https://example.org"}],
}


def _transport(tavily: httpx.Response | None, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url.startswith(tavily_search.TAVILY_URL):
            if tavily is None:
                raise httpx.ConnectError("down")
            return tavily
        if url.startswith("https://en.wikipedia.org/w/rest.php"):
            return httpx.Response(200, json=WIKI_SEARCH)
        if url.startswith("https://en.wikipedia.org/api"):
            return httpx.Response(200, json=WIKI_SUMMARY)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


async def _ask(
    tavily: httpx.Response | None, key: str | None = "test-key"
) -> tuple[str, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    async with Client(build_server(_transport(tavily, seen), key)) as client:
        result = await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
    return str(result.structured_content["result"]), seen


async def test_lists_one_tool() -> None:
    async with Client(build_server(_transport(None, []), "k")) as client:
        assert [t.name for t in await client.list_tools()] == ["web_answer"]


async def test_the_answer_is_cut_to_two_sentences_and_the_request_is_well_formed() -> None:
    text, seen = await _ask(httpx.Response(200, json=ANSWER))
    assert text == "Cormac McCarthy wrote Blood Meridian. It was published in 1985."
    request = seen[0]
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["query"] == "Who wrote Blood Meridian?" and body["include_answer"] == "basic"
    assert body["max_results"] == 3 and body["search_depth"] == tavily_search.SEARCH_DEPTH
    assert len(seen) == 1  # no Wikipedia request when Tavily answered


async def test_the_best_snippet_is_used_when_there_is_no_answer() -> None:
    text, _ = await _ask(httpx.Response(200, json={"answer": None, "results": ANSWER["results"]}))
    assert text == "A snippet."


@pytest.mark.parametrize(
    "tavily",
    [None, httpx.Response(401), httpx.Response(429), httpx.Response(200, text="not json"),
     httpx.Response(200, json={"answer": "", "results": []})],
)  # fmt: skip
async def test_wikipedia_is_the_fallback_when_tavily_does_not_answer(
    tavily: httpx.Response | None,
) -> None:
    text, seen = await _ask(tavily)
    assert text.startswith("Blood Meridian is a 1985 novel")
    assert any("wikipedia.org" in str(r.url) for r in seen)


async def test_without_a_key_tavily_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(tavily_search, "KEY_FILE", Path("/nonexistent/key"))
    text, seen = await _ask(httpx.Response(200, json=ANSWER), key=None)
    assert text.startswith("Blood Meridian is a 1985 novel")
    assert not any(str(r.url).startswith(tavily_search.TAVILY_URL) for r in seen)


def test_the_key_comes_from_the_environment_then_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    keyfile = tmp_path / "key.txt"
    keyfile.write_text("from-file\n")
    monkeypatch.setattr(tavily_search, "KEY_FILE", keyfile)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert load_key() == "from-file"
    monkeypatch.setenv("TAVILY_API_KEY", " from-env ")
    assert load_key() == "from-env"
    monkeypatch.delenv("TAVILY_API_KEY")
    keyfile.write_text("\n")
    assert load_key() is None


def test_spoken_prefers_the_answer_then_a_snippet_then_nothing() -> None:
    assert spoken({"answer": "One. Two. Three."}) == "One. Two."
    assert spoken({"answer": "", "results": [{"content": ""}, {"content": "Second."}]}) == "Second."
    assert spoken({"results": []}) is None


def test_a_lead_in_about_the_sources_is_dropped() -> None:
    said = spoken(
        {"answer": "According to the provided source, the capital is Ulaanbaatar. It is cold."}
    )
    assert said == "The capital is Ulaanbaatar. It is cold."
    assert spoken({"answer": "Based on the sources, it is 8,848 metres."}) == "It is 8,848 metres."
    assert spoken({"answer": "Jollibee is a Filipino chain."}) == "Jollibee is a Filipino chain."


async def test_a_slow_tavily_call_falls_back_to_wikipedia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tavily_search, "TOTAL_TIMEOUT_S", 0.05)

    async def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(tavily_search.TAVILY_URL):
            await asyncio.sleep(1)
            return httpx.Response(200, json=ANSWER)
        if url.startswith("https://en.wikipedia.org/w/rest.php"):
            return httpx.Response(200, json=WIKI_SEARCH)
        return httpx.Response(200, json=WIKI_SUMMARY)

    async with Client(build_server(httpx.MockTransport(handle), "test-key")) as client:
        result = await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
    assert str(result.structured_content["result"]).startswith("Blood Meridian is a 1985 novel")
