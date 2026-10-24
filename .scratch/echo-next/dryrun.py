"""Dry-run the step-2 tickets from their text: apply, run each Proof command, compare at the end.

    python3 .scratch/echo-next/dryrun.py <work dir: a repo copy with its own .venv>

Resets the three source files to the repository's and removes the new tests, then for each ticket
extracts the OLD/NEW blocks and the test files from the ticket's Markdown, applies them, runs the
Proof table's commands and prints each last line. Finally the files must equal the reference
overlay built by build_overlay.sh (passed as REF below).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SRC = ("src/doc_chat/__main__.py", "src/doc_chat/bargein.py", "src/doc_chat/aec.py")
BLOCK = re.compile(r"````python\n(.*?)\n````", re.S)


def main() -> int:
    work, ref = Path(sys.argv[1]), Path(sys.argv[2])
    for rel in SRC:
        shutil.copy(REPO / rel, work / rel)
    for t in sorted((HERE / "tickets").glob("0*.md")):
        for p in (work / "tests/doc_chat").glob("test_*.py"):
            if f"`tests/doc_chat/{p.name}`" in t.read_text() and "new, verbatim" in t.read_text():
                pass
    for t in sorted((HERE / "tickets").glob("0*.md")):
        text = t.read_text()
        for rel in re.findall(r"\| `(tests/[^`]+)` \| new, verbatim", text):
            (work / rel).unlink(missing_ok=True)
    ok = True
    for t in sorted((HERE / "tickets").glob("0*.md")):
        text = t.read_text()
        edits = text.split("## Edits", 1)[1].split("## New test file(s)", 1)[0]
        for m in re.finditer(r"### Edit \d+: `([^`]+)`\n\nOLD:\n\n(````python\n.*?\n````)\n\n"
                             r"NEW:\n\n(````python\n.*?\n````)", edits, re.S):
            rel = m.group(1)
            old = BLOCK.match(m.group(2)).group(1)  # type: ignore[union-attr]
            new = BLOCK.match(m.group(3)).group(1)  # type: ignore[union-attr]
            s = (work / rel).read_text()
            assert s.count(old) == 1, (t.name, rel, old[:60])
            (work / rel).write_text(s.replace(old, new))
        tests = text.split("## New test file(s), verbatim", 1)[1].split("## Proof", 1)[0]
        for m in re.finditer(r"### `([^`]+)`\n\n(````python\n.*?\n````)", tests, re.S):
            (work / m.group(1)).write_text(BLOCK.match(m.group(2)).group(1) + "\n")  # type: ignore[union-attr]
        caps = re.findall(r"\| `([^`]+)` \| [^|]+\| \*\*(\d+)\*\*", text)
        for rel, cap in caps:
            n = len((work / rel).read_text().splitlines())
            if n != int(cap):
                ok = False
            print(f"{t.name}: {rel} {n} lines, cap {cap}{'' if n == int(cap) else '  <-- MISMATCH'}")
        for cmd, exp in re.findall(r"\| \d+ \| `([^`]+)` \| ([^|]+) \|", text):
            r = subprocess.run(cmd, shell=True, cwd=work, capture_output=True, text=True)
            last = (r.stdout.strip().splitlines() or r.stderr.strip().splitlines() or [""])[-1]
            print(f"   rc={r.returncode} {cmd[:70]}\n      got: {last[:110]}\n      expected: "
                  f"{exp.strip()[:110]}")
    for rel in SRC:
        same = (work / rel).read_text() == (ref / rel).read_text()
        ok &= same
        print(f"final {rel}: {'identical to the reference' if same else 'DIFFERENT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
