from pathlib import Path

import pytest
from fastmcp import Client

from voice_agent.mcp_servers.docs import DEFAULT_CORPUS, build_server

CORPUS = Path(__file__).resolve().parents[2] / "corpus" / "baby-onit"

# question -> a source file that must be among the top 3 passages (the answer is stated there)
QUESTIONS = {
    "What is baby-onit?": ("00-overview", "13-from"),
    "How does baby-onit work?": ("00-overview",),
    "What is onit?": ("00-overview", "13-from"),
    "Where do baby-onit's API keys live?": ("03-secrets",),
    "Which tools does baby-onit ship?": ("06-tools", "14-about"),
    "How many tools does baby-onit have?": ("06-tools", "14-about"),
    "How is baby-onit different from onit?": ("13-from", "00-overview"),
    "What does the baby-onit agent loop do?": ("08-the-agent",),
    "What does the agent loop do?": ("08-the-agent",),
    "What is the NEVER_ASK_COMMANDS list in baby-onit?": ("06-tools",),
    "How does baby-onit remember earlier turns in a session?": ("09-session",),
    "What does baby-onit's doctor command check?": ("11-doctor",),
    "What does the doctor command do?": ("11-doctor",),
    "What does the baby-onit setup wizard do?": ("12-setup",),
    "Which model servers can baby-onit talk to?": ("04-providers", "00-overview"),
    "What are the config defaults for baby-onit?": ("02-one-config",),
    "What does the system prompt tell the model?": ("05-system",),
    "How does the dispatch function work?": ("07-registry",),
    "What does the text UI show?": ("10-the-text",),
    "What commands can baby-onit run?": ("12-setup", "00-overview"),
    "Why does baby-onit exist?": ("00-why", "00-overview"),
}


async def _search(query: str, top_k: int = 3) -> list[dict[str, str]]:
    async with Client(build_server(CORPUS)) as client:
        result = await client.call_tool("search_docs", {"query": query, "top_k": top_k})
    return result.structured_content["result"]


def test_the_default_corpus_is_the_shipped_baby_onit_folder() -> None:
    assert Path("corpus/baby-onit") == DEFAULT_CORPUS and CORPUS.is_dir()
    assert len(list(CORPUS.glob("*.md"))) == 16


async def test_lists_one_tool_that_names_the_topic() -> None:
    async with Client(build_server(CORPUS)) as client:
        tools = await client.list_tools()
    assert [t.name for t in tools] == ["search_docs"]
    assert "baby-onit" in (tools[0].description or "")


@pytest.mark.parametrize("question", list(QUESTIONS))
async def test_the_passage_that_states_the_answer_is_in_the_top_three(question: str) -> None:
    hits = await _search(question)
    sources = [h["source"] for h in hits]
    assert len(hits) == 3
    assert any(s.startswith(want) for s in sources for want in QUESTIONS[question]), sources


async def test_search_returns_passages_with_sources() -> None:
    hits = await _search("Where do baby-onit's API keys live?")
    assert hits[0]["source"] == "03-secrets.md"
    assert "keychain" in hits[0]["text"] and "secrets.yaml" in hits[0]["text"]


async def test_the_documents_own_wording_for_the_tool_count_is_kept() -> None:
    text = " ".join(h["text"] for h in await _search("How many tools does baby-onit ship?"))
    assert "ships eight tools" in text  # the PDF says eight and lists nine; both are stated


async def test_top_k_is_clamped() -> None:
    assert len(await _search("agent loop", top_k=1)) == 1
    assert len(await _search("agent loop", top_k=50)) == 8
