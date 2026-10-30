"""Dry run: build every file from the ticket TEXT in a clean copy, then run each Proof command as written."""
import re, subprocess, sys
from pathlib import Path

copy = Path(sys.argv[1])
ok = True
for ticket in sorted((copy / ".scratch/onit-tools/tickets").glob("0*.md")):
    text = ticket.read_text()
    for m in re.finditer(r"#### `([^`]+)` \(new, verbatim\)\n\n```python\n(.*?)```\n", text, re.S):
        path = copy / m.group(1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(m.group(2))
    for m in re.finditer(r"#### `([^`]+)` \(edit list.*?\n(.*?)(?=\n#### |\n### )", text, re.S):
        path = copy / m.group(1)
        body = path.read_text()
        pairs = re.findall(r"OLD:\n\n```text\n(.*?)```\n\nNEW:\n\n```text\n(.*?)```\n", m.group(2), re.S)
        for old, new in pairs:
            assert body.count(old) == 1, f"{ticket.name}: {m.group(1)} old block occurs {body.count(old)}x: {old[:50]!r}"
            body = body.replace(old, new, 1)
        path.write_text(body)
        print(f"{ticket.name}: {m.group(1)}: {len(pairs)} edits")
    for m in re.finditer(r"^\d+\. `([^`]+)`\n   Expected last line \(ignore the timing\): `([^`]*)`", text, re.S | re.M):
        cmd, expected = m.groups()
        out = subprocess.run(cmd, shell=True, cwd=copy, capture_output=True, text=True)
        last = (out.stdout + out.stderr).strip().splitlines()[-1] if (out.stdout + out.stderr).strip() else ""
        last = re.sub(r" in [0-9.]+s$", "", last)
        good = last == expected
        ok &= good
        print(("PASS " if good else "FAIL ") + f"{ticket.name}: {cmd[:70]}... -> {last!r} (expected {expected!r})")
sys.exit(0 if ok else 1)
