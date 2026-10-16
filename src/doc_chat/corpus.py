"""Corpus loading and text splitting for voice doc-chat (ticket 24)."""

from __future__ import annotations

import re
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


_STOPWORDS = frozenset(
    "a an and are as at be by can did do does for from has have how i in is it its me of on or "
    "tell that the their then there this to was were what when where which who whom whose why "
    "will with you your about give say".split()
)


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in re.findall(r"[a-z0-9']+", text.lower().replace("\u2019", "'")):
        word = raw.removesuffix("'s").strip("'")
        if word.endswith(("xes", "ches", "shes", "sses")):
            word = word[:-2]
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            word = word[:-1]
        if len(word) > 5 and word.endswith("ing"):
            word = word[:-3]
        elif len(word) > 4 and word.endswith("ed"):
            word = word[:-2]
        if word and word not in _STOPWORDS:
            tokens.append(word)
    return tokens
