"""Write the names in a folder of documents as a speech-server names file.

    uv run python scripts/make_entities.py corpus/harry-potter -o config/entities-docs.json
    uv run --group laptop python -m lan_speech --stt-model parakeet \
        --entities config/entities-docs.json

Each name gets the category "doc"; doc-chat sends the optional slot "doc?" so names are
repaired when heard and questions without one are left alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from doc_chat.names import names_from_folder  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("config/entities-docs.json"))
    ap.add_argument("--min-count", type=int, default=2, help="single words must appear this often")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--category", default="doc")
    args = ap.parse_args()
    names = names_from_folder(args.docs, min_count=args.min_count, limit=args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({n: args.category for n in names}, indent=1, ensure_ascii=False))
    print(f"{len(names)} names -> {args.out}")
    print("first 40:", ", ".join(names[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
