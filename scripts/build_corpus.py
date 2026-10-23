"""Split agy research reports into a doc_chat corpus folder.

    uv run python scripts/build_corpus.py corpus/cormac-mccarthy \
        .scratch/speech-hosting/AGY-MCCARTHY-*.md

Each report holds ``=== FILE: NN-name.md ===`` blocks, then ``=== SOURCES ===`` and
``=== UNVERIFIED ===`` blocks. Only the file blocks go into the corpus folder (every *.md and *.txt
in it is indexed, so sources would be retrieved as if they were facts); the sources and the
unverified claims are written next to the reports as ``<folder-name>-SOURCES.md`` and
``<folder-name>-UNVERIFIED.md`` in ``--notes``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FILE_RE = re.compile(r"^=== FILE: ([A-Za-z0-9._-]+\.md) ===\s*$", re.M)


def split_report(text: str) -> tuple[dict[str, str], str, str]:
    """(files, sources, unverified) from one report."""
    unverified = ""
    sources = ""
    if "=== UNVERIFIED ===" in text:
        text, unverified = text.split("=== UNVERIFIED ===", 1)
    if "=== SOURCES ===" in text:
        text, sources = text.split("=== SOURCES ===", 1)
    marks = list(FILE_RE.finditer(text))
    files: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        files[m.group(1)] = text[m.end() : end].strip() + "\n"
    return files, sources.strip(), unverified.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", type=Path, help="corpus folder to write")
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--notes", type=Path, default=Path(".scratch/speech-hosting"))
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    args.notes.mkdir(parents=True, exist_ok=True)
    all_sources, all_unverified, total_words, count = [], [], 0, 0
    for report in args.reports:
        files, sources, unverified = split_report(report.read_text(encoding="utf-8"))
        if not files:
            print(f"{report.name}: no file blocks found", file=sys.stderr)
            continue
        for name, body in files.items():
            (args.out / name).write_text(body, encoding="utf-8")
            words = len(body.split())
            total_words += words
            count += 1
            print(f"  {name:<46}{words:>6} words")
        all_sources.append(f"## {report.name}\n\n{sources}\n")
        all_unverified.append(f"## {report.name}\n\n{unverified}\n")
    stem = args.out.name
    (args.notes / f"{stem}-SOURCES.md").write_text("\n".join(all_sources), encoding="utf-8")
    (args.notes / f"{stem}-UNVERIFIED.md").write_text("\n".join(all_unverified), encoding="utf-8")
    print(f"{count} files, {total_words} words -> {args.out}")
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
