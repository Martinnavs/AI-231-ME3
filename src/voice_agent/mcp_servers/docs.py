"""Docs MCP server (fastmcp): BM25 search over a doc-chat corpus folder.

uv run --group laptop python -m voice_agent.mcp_servers.docs --corpus corpus/baby-onit
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from fastmcp import FastMCP
from langchain_community.retrievers import BM25Retriever

from doc_chat.corpus import load_and_split_corpus, tokenize

DEFAULT_CORPUS = Path("corpus/baby-onit")
DEFAULT_TOPIC = (
    "baby-onit, the single-file agent harness distilled from onit (its config, secrets, providers, "
    "system prompt, tools, dispatch, agent loop, session memory, text UI, doctor and setup)"
)
# 700 characters: with the shorter, name-heavy sections of the baby-onit corpus, 1,000-character
# chunks ranked a section that merely repeats the name above the one that answers (2026-11-11)
CHUNK_SIZE = 700


def build_server(corpus: Path, topic: str = DEFAULT_TOPIC) -> FastMCP:
    """A FastMCP server with one tool, search_docs, over the chunks of `corpus`."""
    chunks = load_and_split_corpus(corpus, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_SIZE // 10)
    retriever = BM25Retriever.from_documents(chunks, k=3, preprocess_func=tokenize)
    server = FastMCP("docs")

    @server.tool(
        description=(
            f"Search the documents about {topic} and return the best-matching passages with "
            "their source file. Use it, and only it, for any question on that topic and answer "
            "from the passages. Write the query as a short self-contained question."
        )
    )
    def search_docs(query: str, top_k: int = 3) -> list[dict[str, str]]:
        retriever.k = max(1, min(top_k, 8))
        return [
            {"source": Path(str(doc.metadata.get("source", ""))).name, "text": doc.page_content}
            for doc in retriever.invoke(query)
        ]

    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    args = parser.parse_args(argv)
    build_server(args.corpus, args.topic).run(show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
