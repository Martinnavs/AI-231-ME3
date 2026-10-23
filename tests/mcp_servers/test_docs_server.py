from pathlib import Path

from fastmcp import Client

from voice_agent.mcp_servers.docs import build_server

CORPUS = Path(__file__).resolve().parents[2] / "corpus" / "cormac-mccarthy"


async def _search(query: str, top_k: int = 3) -> list[dict[str, str]]:
    async with Client(build_server(CORPUS)) as client:
        result = await client.call_tool("search_docs", {"query": query, "top_k": top_k})
    return result.structured_content["result"]


async def test_lists_one_tool() -> None:
    async with Client(build_server(CORPUS)) as client:
        tools = await client.list_tools()
    assert [t.name for t in tools] == ["search_docs"]
    assert "Cormac McCarthy" in (tools[0].description or "")


async def test_search_returns_passages_with_sources() -> None:
    hits = await _search("What is The Road about?")
    assert [h["source"] for h in hits] == ["23-the-road.md"] * 3
    assert all("The Road" in h["text"] for h in hits)


async def test_top_k_is_clamped() -> None:
    assert len(await _search("The Road", top_k=1)) == 1
    assert len(await _search("The Road", top_k=50)) == 8
