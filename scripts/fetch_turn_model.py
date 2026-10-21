"""Download the Turnsense text turn-end model (Apache-2.0) into models/turn-detector/.

uv run python scripts/fetch_turn_model.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "https://huggingface.co/latishab/turnsense/resolve/main"
FILES = {
    "model_quantized.onnx": "a423adf55f5f33cf4ee9e3fe73ec133d0106affae3aa14693417b4a1c79e2df8",
    "tokenizer.json": None,
}


def main() -> int:
    out = Path("models/turn-detector")
    out.mkdir(parents=True, exist_ok=True)
    for name, sha in FILES.items():
        target = out / name
        if not target.exists():
            print(f"downloading {name} ...", flush=True)
            urllib.request.urlretrieve(f"{REPO}/{name}", target)  # noqa: S310
        if sha is not None:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != sha:
                print(f"{name}: checksum mismatch", file=sys.stderr)
                return 1
        print(f"{name}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
