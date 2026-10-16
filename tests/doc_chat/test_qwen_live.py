"""Live test for Qwen backend (P7).

Marked `live`: left for the human runner with real credentials and VOICE_AGENT_LIVE=1.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_community")

from langchain_community.retrievers import BM25Retriever  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

from doc_chat.chain import build_answer_chain  # noqa: E402
from doc_chat.qwen import build_qwen_chat_model  # noqa: E402


@pytest.mark.live
def test_qwen_live() -> None:
    llm = build_qwen_chat_model()
    doc = Document(
        page_content="Antigravity is an AI coding assistant designed by Google DeepMind.",
        metadata={"source": "about.txt"},
    )
    retriever = BM25Retriever.from_documents([doc], k=1)
    chain = build_answer_chain(retriever, max_words=30, llm=llm)

    ans = chain.invoke("What is Antigravity?")
    assert len(ans.text) > 0
    assert ans.source == "about.txt"
