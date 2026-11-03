import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google.genai import types

from voice_pipeline.search_count import SearchCounter, queries_in, tool_tokens_in


def chunk(queries: list[str] | None = None, tool_tokens: int | None = None) -> Any:
    metadata = types.GroundingMetadata(web_search_queries=queries) if queries else None
    return SimpleNamespace(
        candidates=[SimpleNamespace(grounding_metadata=metadata)],
        usage_metadata=SimpleNamespace(tool_use_prompt_token_count=tool_tokens),
    )


async def stream(*chunks: Any) -> Any:
    for c in chunks:
        yield c


async def drain(counter: SearchCounter, *chunks: Any) -> list[Any]:
    return [c async for c in counter.watch(stream(*chunks))]


def test_queries_and_tool_tokens_are_read_from_a_chunk() -> None:
    assert queries_in(chunk(["a", "b"])) == 2 and queries_in(chunk()) == 0
    assert queries_in(SimpleNamespace(candidates=None)) == 0
    assert tool_tokens_in(chunk(None, 1200)) == 1200 and tool_tokens_in(chunk()) == 0


async def test_the_largest_query_list_of_a_request_counts_once(tmp_path: Path) -> None:
    counter = SearchCounter(tmp_path / "l.jsonl", month="2026-11")
    passed = await drain(counter, chunk(["a"]), chunk(["a", "b"]), chunk(["a", "b"], 900))
    assert len(passed) == 3  # every chunk goes on unchanged
    assert (counter.searches, counter.requests, counter.tool_tokens) == (2, 1, 900)
    rows = [json.loads(x) for x in (tmp_path / "l.jsonl").read_text().splitlines()]
    assert sum(r["searches"] for r in rows) == 2  # 1, then the increase of 1: counted once each
    assert {(r["month"], r["source"]) for r in rows} == {("2026-11", "builtin")}


async def test_a_request_without_a_search_writes_nothing(tmp_path: Path) -> None:
    counter = SearchCounter(tmp_path / "l.jsonl")
    await drain(counter, chunk(), chunk())
    assert counter.searches == 0 and not (tmp_path / "l.jsonl").exists()


async def test_a_search_is_counted_even_if_the_reader_stops_after_that_chunk(
    tmp_path: Path,
) -> None:
    counter = SearchCounter(tmp_path / "l.jsonl")
    watched = counter.watch(stream(chunk(), chunk(["a"]), chunk(["a"])))
    await watched.__anext__()
    await watched.__anext__()  # the chunk that reports the search; the reader never asks for more
    assert counter.searches == 1 and counter.requests == 1


def test_the_month_total_is_shared_with_the_other_path_and_the_cap_applies(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    path.write_text('{"month": "2026-11", "searches": 4}\n{"month": "2026-10", "searches": 50}\n')
    counter = SearchCounter(path, cap=5, month="2026-11")
    assert counter.month_searches == 4 and not counter.exhausted
    counter.record(1, 0, 0.1)
    assert counter.exhausted and SearchCounter(path, cap=5, month="2026-11").exhausted


def test_a_bad_or_missing_ledger_counts_from_zero(tmp_path: Path) -> None:
    (tmp_path / "bad.jsonl").write_text("not json\n")
    assert SearchCounter(tmp_path / "bad.jsonl").month_searches == 0
    assert SearchCounter(tmp_path / "none.jsonl").month_searches == 0
    assert SearchCounter(None).month_searches == 0


def test_over_the_cap_the_request_loses_google_search_and_its_option() -> None:
    config = types.GenerateContentConfig(
        tools=[
            types.Tool(function_declarations=[types.FunctionDeclaration(name="weather")]),
            types.Tool(google_search=types.GoogleSearch()),
        ],
        tool_config=types.ToolConfig(include_server_side_tool_invocations=True),
    )
    cut = SearchCounter.without_search(config)
    assert [bool(t.google_search) for t in cut.tools or []] == [False]
    assert cut.tool_config is None and len(config.tools or []) == 2  # the original is untouched


async def test_attach_counts_through_the_real_call_shape(tmp_path: Path) -> None:
    seen: list[Any] = []

    async def generate_content_stream(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["config"])
        return stream(chunk(["q"]))

    models = SimpleNamespace(generate_content_stream=generate_content_stream)
    llm = SimpleNamespace(_client=SimpleNamespace(aio=SimpleNamespace(models=models)))
    counter = SearchCounter(tmp_path / "l.jsonl", cap=1, month="2026-11")
    counter.attach(llm)
    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
    async for _ in await llm._client.aio.models.generate_content_stream(config=config):
        pass
    assert counter.searches == 1 and seen[0].tools == config.tools  # under the cap: unchanged
    async for _ in await llm._client.aio.models.generate_content_stream(config=config):
        pass
    assert seen[1].tools is None  # at the cap: no search tool in the request
