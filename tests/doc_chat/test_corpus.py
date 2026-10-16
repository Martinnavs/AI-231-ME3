"""Tests for doc_chat corpus loading and splitting (P1)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_text_splitters")

from doc_chat.corpus import load_and_split_corpus  # noqa: E402


def test_corpus_split_and_sources(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    file1 = docs_dir / "guide.md"
    file1.write_text("# Project Guide\n\nThis is a test document with useful guidance.\n" * 5)

    file2 = docs_dir / "notes.txt"
    file2.write_text("Here are some notes about the project architecture.\n" * 5)

    chunks = load_and_split_corpus(docs_dir, chunk_size=100, chunk_overlap=20)
    assert len(chunks) > 0

    sources = {c.metadata.get("source") for c in chunks}
    assert "guide.md" in sources
    assert "notes.txt" in sources


def test_empty_corpus_exits_2(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_docs"
    empty_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        load_and_split_corpus(empty_dir)
    assert exc_info.value.code == 2


def test_missing_corpus_exits_2(tmp_path: Path) -> None:
    missing_dir = tmp_path / "non_existent"

    with pytest.raises(SystemExit) as exc_info:
        load_and_split_corpus(missing_dir)
    assert exc_info.value.code == 2


def test_tokenize_matches_spoken_uppercase_and_plurals():
    from doc_chat.corpus import tokenize

    assert tokenize("WHAT IS A HORCRUX?") == ["horcrux"]
    assert tokenize("The Horcruxes") == ["horcrux"]
    assert tokenize("Dumbledore's") == ["dumbledore"]
    assert tokenize("WHO KILLED DUMBLEDORE") == ["kill", "dumbledore"]
