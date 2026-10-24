"""Dry run: apply a ticket from its text alone to a copy, then run its Proof commands.

    python3 .scratch/latency/apply_ticket.py <copy root> <ticket.md>
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BLOCK = re.compile(r"````[a-z]*\n(.*?)\n````", re.S)


def main() -> None:
    root, ticket = Path(sys.argv[1]), Path(sys.argv[2]).read_text()
    edits, new = ticket.split("## Edits", 1)[1].split("## New files, verbatim", 1)
    new, proof = new.split("## Proof", 1)
    for sec in re.split(r"^### Edit \d+: ", edits, flags=re.M)[1:]:
        rel = sec.split("`")[1]
        old, repl = BLOCK.findall(sec)
        path = root / rel
        text = path.read_text()
        assert text.count(old) == 1, (rel, text.count(old), old[:80])
        path.write_text(text.replace(old, repl))
    for sec in re.split(r"^### ", new, flags=re.M)[1:]:
        rel = sec.split("`")[1]
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(BLOCK.findall(sec)[0] + "\n")
    for line in proof.splitlines():
        m = re.match(r"\| (\d+) \| `(.*)` \| (.*) \|$", line)
        if not m:
            continue
        r = subprocess.run(m[2], shell=True, cwd=root, capture_output=True, text=True)
        out = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()]
        print(f"{m[1]} rc={r.returncode} expected={m[3]!r}\n   last: {out[-1] if out else ''}")


if __name__ == "__main__":
    main()
