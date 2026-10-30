"""Apply edits.py to the overlay: copy each target from the repo, then replace each old block once."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from edits import EDITS
repo, overlay = Path(sys.argv[1]), Path(sys.argv[2])
for rel in sorted({e[0] for e in EDITS}):
    dst = overlay / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(repo / rel, dst)
for rel, old, new in EDITS:
    text = (overlay / rel).read_text()
    assert text.count(old) == 1, f"{rel}: old block occurs {text.count(old)} times: {old[:50]!r}"
    (overlay / rel).write_text(text.replace(old, new, 1))
print("edits applied:", len(EDITS))
