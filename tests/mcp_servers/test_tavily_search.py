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
    async with Client(build_server(_transport(tavily, seen), key, None)) as client:
        result = await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
    return str(result.structured_content["result"]), seen


async def test_lists_one_tool() -> None:
    async with Client(build_server(_transport(None, []), "k", None)) as client:
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
    results = [{"content": "Blood Meridian snippet.", "url": "https://example.org"}]
    text, _ = await _ask(httpx.Response(200, json={"answer": None, "results": results}))
    assert text == "Blood Meridian snippet."


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

    async with Client(build_server(httpx.MockTransport(handle), "test-key", None)) as client:
        result = await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
    assert str(result.structured_content["result"]).startswith("Blood Meridian is a 1985 novel")


async def test_a_repeated_question_is_answered_from_memory() -> None:
    seen: list[httpx.Request] = []
    async with Client(
        build_server(_transport(httpx.Response(200, json=ANSWER), seen), "test-key", None)
    ) as client:
        first = await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
        again = await client.call_tool("web_answer", {"query": "who wrote  blood meridian"})
    assert first.structured_content == again.structured_content
    assert len([r for r in seen if str(r.url).startswith(tavily_search.TAVILY_URL)]) == 1


async def test_the_cache_expires() -> None:
    now = [0.0]
    seen: list[httpx.Request] = []
    server = build_server(
        _transport(httpx.Response(200, json=ANSWER), seen), "test-key", None, clock=lambda: now[0]
    )
    async with Client(server) as client:
        await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
        now[0] = tavily_search.CACHE_TTL_S + 1
        await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
    assert len([r for r in seen if str(r.url).startswith(tavily_search.TAVILY_URL)]) == 2


async def test_a_fallback_answer_is_not_cached() -> None:
    seen: list[httpx.Request] = []
    async with Client(
        build_server(_transport(httpx.Response(500), seen), "test-key", None)
    ) as client:
        await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
        await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
    assert len([r for r in seen if str(r.url).startswith(tavily_search.TAVILY_URL)]) == 2


async def test_the_connection_is_kept_open_and_pinged_while_in_use() -> None:
    seen: list[httpx.Request] = []
    server = build_server(_transport(httpx.Response(200, json=ANSWER), seen), "test-key", 0.05)
    async with Client(server) as client:
        await client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
        await asyncio.sleep(0.3)
    pings = [r for r in seen if str(r.url) == tavily_search.PING_URL]
    assert len(pings) >= 2  # opened at start, then kept warm
    assert all(r.method == "GET" and "Authorization" not in r.headers for r in pings)


def test_wordings_of_one_question_share_content_words() -> None:
    words = tavily_search.content_words
    assert words("When was the EDSA revolution?") == words("the EDSA revolution")
    assert words("Who won?!") == words("  who   WON ")
    assert words("What is the Eiffel Tower's height?") == words("Eiffel Tower height")


def test_only_close_wordings_count_as_the_same_question() -> None:
    same, words = tavily_search.same_question, tavily_search.content_words
    assert same(
        words("Who won the 2022 FIFA World Cup?"), words("Who won the FIFA World Cup in 2022?")
    )
    assert not same(words("Who won the 2022 World Cup?"), words("Who won the 2018 World Cup?"))
    assert not same(words("Sorry, I mean Sinigang."), words("What is sinigang?"))
    assert not same(words("Paris"), words("Paris weather"))  # one word each is never "close"


class _Slow(httpx.AsyncBaseTransport):
    """Tavily answers after a delay, so a second call can arrive while the first is running."""

    def __init__(self, delay: float = 0.2) -> None:
        self.delay, self.searches = delay, 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(tavily_search.TAVILY_URL):
            self.searches += 1
            await asyncio.sleep(self.delay)
            return httpx.Response(200, json=ANSWER)
        return httpx.Response(404)


async def test_a_second_wording_joins_the_search_already_running() -> None:
    transport = _Slow()
    async with Client(build_server(transport, "test-key", None)) as client:
        first, second = await asyncio.gather(
            client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"}),
            client.call_tool("web_answer", {"query": "who wrote the novel Blood Meridian"}),
        )
    assert transport.searches == 1
    assert first.structured_content == second.structured_content


async def test_a_different_question_is_searched_on_its_own() -> None:
    transport = _Slow()
    async with Client(build_server(transport, "test-key", None)) as client:
        await asyncio.gather(
            client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"}),
            client.call_tool("web_answer", {"query": "Who wrote The Road?"}),
        )
    assert transport.searches == 2


async def test_giving_up_on_a_joined_search_does_not_cancel_it_for_the_others() -> None:
    transport = _Slow()
    async with Client(build_server(transport, "test-key", None)) as client:
        loser = asyncio.ensure_future(
            client.call_tool("web_answer", {"query": "Who wrote Blood Meridian?"})
        )
        await asyncio.sleep(0.05)
        winner = asyncio.ensure_future(
            client.call_tool("web_answer", {"query": "who wrote Blood Meridian"})
        )
        await asyncio.sleep(0.05)
        loser.cancel()
        result = await winner
    assert "Blood Meridian" in str(result.structured_content["result"])
    assert transport.searches == 1


def _data(answer: str, *snippets: str) -> dict[str, object]:
    return {"answer": answer, "results": [{"title": "", "content": c, "url": ""} for c in snippets]}


def test_a_year_that_no_source_mentions_is_unconfirmed() -> None:
    answer = "The 1987 prize went to Bednorz and Mueller."
    data = _data(answer, "Nobel Prize in Physics 1987 winners.")
    assert tavily_search.unconfirmed("Who won the 2087 Nobel Prize in Physics?", answer, data) == [
        "2087"
    ]
    assert tavily_search.unconfirmed("Who won the 1987 Nobel Prize in Physics?", answer, data) == []


def test_a_number_with_commas_is_found_in_either_form() -> None:
    answer = "It has 1,234 residents."
    assert tavily_search.unconfirmed("Is the 1234 count right?", answer, _data(answer)) == []
    assert tavily_search.unconfirmed("Is the 1,234 count right?", "1234 people", _data("x")) == []


def test_a_name_is_checked_with_some_tolerance_for_spelling() -> None:
    answer = "Émile Nouguier and Gustave Eiffel designed it."
    data = _data(answer, "Maurice Koechlin helped.")
    assert tavily_search.unconfirmed("Who was Gustave Eiffel's partner?", answer, data) == []
    assert tavily_search.unconfirmed("Did Emile Nouguier help?", answer, data) == []  # accent
    assert tavily_search.unconfirmed("Did Thomas Edison help?", answer, data) == [
        "Thomas",
        "Edison",
    ]


def test_the_first_word_and_pronouns_are_not_taken_for_names() -> None:
    answer = "Paris is the capital of France."
    assert tavily_search.unconfirmed("Who knows? I think Paris is.", answer, _data(answer)) == []


async def test_an_unconfirmed_year_is_said_so_and_the_hedge_is_cached() -> None:
    seen: list[httpx.Request] = []
    ask = {"query": "Who won the 2087 Blood Meridian prize?"}
    async with Client(
        build_server(_transport(httpx.Response(200, json=ANSWER), seen), "test-key", None)
    ) as client:
        first = await client.call_tool("web_answer", ask)
        again = await client.call_tool("web_answer", ask)
    text = str(first.structured_content["result"])
    assert text.startswith("I couldn't confirm 2087 in what I found. The closest result says: ")
    assert "Blood Meridian" in text and first.structured_content == again.structured_content
    assert len(seen) == 1
