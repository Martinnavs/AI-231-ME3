"""Write the names in a folder of documents as a speech-server names file.

    uv run python scripts/make_entities.py corpus/baby-onit --merge config/entities.json \
        -o config/entities-baby-onit.json
    uv run --group laptop python -m lan_speech --stt-model parakeet \
        --entities config/entities-baby-onit.json

The extractor finds capitalised names only: the shipped config/entities-baby-onit.json was curated
by hand afterwards (it missed baby-onit, onit, vLLM, SGLang, OpenRouter, in spoken form).

Each name gets the category "doc"; doc-chat sends the optional slot "doc?" (in agent mode too)
so names are repaired when heard and questions without one are left alone.

    --skip-common-words            drop one-word names that are ordinary English ("God", "Road"):
                                   as repair targets they turn "good" into "God" (2026-11-04)
    --merge config/entities.json   start from the general list and add the document names
                                   (a name already in it keeps its category), for one file
                                   that serves both the hotwords and the repair.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from doc_chat.names import names_from_folder  # noqa: E402


def without_common_words(names: list[str], known: frozenset[str]) -> list[str]:
    """``names`` minus single words that are in the English dictionary ``known``."""
    return [n for n in names if " " in n or n.lower() not in known]


def merge_names(base: dict[str, str], names: list[str], category: str) -> dict[str, str]:
    """``base`` plus ``names`` as ``category``; a name already in ``base`` (any case) stays."""
    merged = dict(base)
    seen = {n.casefold() for n in base}
    for n in names:
        if n.casefold() not in seen:
            seen.add(n.casefold())
            merged[n] = category
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("config/entities-docs.json"))
    ap.add_argument("--min-count", type=int, default=2, help="single words must appear this often")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--category", default="doc")
    ap.add_argument("--skip-common-words", action="store_true", help="see the module docstring")
    ap.add_argument(
        "--merge", type=Path, help="names file (name -> category) to start from; its names stay"
    )
    args = ap.parse_args()
    names = names_from_folder(args.docs, min_count=args.min_count, limit=args.limit)
    if args.skip_common_words:
        import cmudict

        names = without_common_words(names, frozenset(cmudict.words()))
    base: dict[str, str] = {}
    if args.merge:
        loaded = json.loads(args.merge.read_text(encoding="utf-8"))
        base = loaded if isinstance(loaded, dict) else {str(n): "other" for n in loaded}
    result = merge_names(base, names, args.category)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(f"{len(result)} names ({len(result) - len(base)} from the documents) -> {args.out}")
    print("first 40:", ", ".join(names[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
