"""Corpus loading and text splitting for voice doc-chat (ticket 24)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.documents import Document


def load_and_split_corpus(
    docs_dir: Path | str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[Document]:
    """Load all *.md and *.txt files in docs_dir and split into chunks with source metadata."""
    dir_path = Path(docs_dir)
    if not dir_path.is_dir():
        print(f"no documents found in {docs_dir}", file=sys.stderr)
        sys.exit(2)

    files = sorted(list(dir_path.rglob("*.md")) + list(dir_path.rglob("*.txt")))
    if not files:
        print(f"no documents found in {docs_dir}", file=sys.stderr)
        sys.exit(2)

    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    raw_docs: list[Document] = []
    for file_path in files:
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if content.strip():
            raw_docs.append(Document(page_content=content, metadata={"source": file_path.name}))

    if not raw_docs:
        print(f"no documents found in {docs_dir}", file=sys.stderr)
        sys.exit(2)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    chunks = splitter.split_documents(raw_docs)
    if not chunks:
        print(f"no documents found in {docs_dir}", file=sys.stderr)
        sys.exit(2)

    return list(chunks)
