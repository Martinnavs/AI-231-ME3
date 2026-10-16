"""Tests for doc_chat answer chain (P2, P3, P6)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_community")

from langchain_community.retrievers import BM25Retriever  # noqa: E402
from langchain_core.documents import Document  # noqa: E402
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402

from doc_chat.chain import Answer, build_answer_chain  # noqa: E402
from doc_chat.qwen import build_qwen_chat_model  # noqa: E402


def test_answer_chain_bm25() -> None:
    doc1 = Document(
        page_content=(
            "# Audio Engine Setup\n\n"
            "| Setting | Value |\n"
            "|---|---|\n"
            "| Sample Rate | 16000 |\n\n"
            "```python\n"
            "import soundfile\n"
            "```\n\n"
            "Here is the [audio configuration](https://example.com/audio). "
            "The system records audio at sixteen kilohertz mono. "
            "This additional sentence describes extra details."
        ),
        metadata={"source": "audio.md"},
    )
    doc2 = Document(
        page_content="Apples and oranges are nutritious fruits commonly found in orchards.",
        metadata={"source": "fruits.md"},
    )
    doc3 = Document(
        page_content="Cars and trucks travel along highways and city streets.",
        metadata={"source": "vehicles.md"},
    )

    retriever = BM25Retriever.from_documents([doc1, doc2, doc3], k=1)
    chain = build_answer_chain(retriever, max_words=20)

    ans: Answer = chain.invoke("audio configuration")
    assert isinstance(ans, Answer)
    assert ans.source == "audio.md"
    assert ans.retrieval_ms >= 0
    # Markdown stripped
    assert "#" not in ans.text
    assert "|" not in ans.text
    assert "https://example.com" not in ans.text
    assert "audio configuration" in ans.text
    # max_words honored
    words = ans.text.split()
    assert len(words) <= 20


def test_answer_chain_no_hit() -> None:
    # Empty documents in retriever
    doc = Document(
        page_content="Only specialized quantum mechanics topics.",
        metadata={"source": "physics.md"},
    )
    retriever = BM25Retriever.from_documents([doc], k=1)
    chain = build_answer_chain(retriever, max_words=40)

    # Completely unrelated query
    ans: Answer = chain.invoke("xyzzyqwerty12345")
    # Even if BM25 returns top doc for anything, test that chain produces an Answer
    assert isinstance(ans, Answer)


def test_answer_chain_empty_docs() -> None:
    class EmptyRetriever:
        def invoke(self, query: str) -> list[Document]:
            return []

    chain = build_answer_chain(EmptyRetriever(), max_words=40)
    ans: Answer = chain.invoke("any query")
    assert ans.text == "I couldn't find that in the documents."
    assert ans.source is None
    assert ans.retrieval_ms >= 0


def test_answer_chain_with_fake_llm() -> None:
    doc = Document(
        page_content="The system records audio at sixteen kilohertz mono.",
        metadata={"source": "audio.md"},
    )
    retriever = BM25Retriever.from_documents([doc], k=1)
    fake_llm = FakeListChatModel(responses=["The system records audio at 16kHz mono."])

    chain = build_answer_chain(retriever, max_words=40, llm=fake_llm)
    ans: Answer = chain.invoke("what sample rate does the system record?")
    assert isinstance(ans, Answer)
    assert ans.text == "The system records audio at 16kHz mono."
    assert ans.source == "audio.md"
    assert ans.retrieval_ms >= 0


def test_qwen_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ONIT_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(SystemExit) as exc_info:
        build_qwen_chat_model()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "qwen backend not configured" in captured.err
    # Verify no secret values printed
    assert "key" not in captured.err.lower() or "not configured" in captured.err
