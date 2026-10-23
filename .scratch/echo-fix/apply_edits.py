"""apply_edits.py <edits module> <root>: apply every (old, new) once; fail if old is not unique."""
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("edits", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
root = Path(sys.argv[2])
for rel, pairs in m.EDITS.items():
    p = root / rel; s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, f"{rel}: old block occurs {s.count(old)} times:\n{old[:120]}"
        s = s.replace(old, new)
    p.write_text(s); print("edited", rel, len(pairs))
