"""Download the laptop speech-server model files into models/ and verify SHA-256.

Run: uv run python scripts/fetch_laptop_models.py [--models models] [--check] [--server-only]
Without --server-only it also fetches Parakeet-TDT 0.6B v2 int8 (~660 MB) for
scripts/stt_hotwords_eval.py.
Exit 2 names the file on a missing download or checksum mismatch. The manifest lives in
src/lan_speech/models.py so the server and this script share one list.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from lan_speech.models import (
    KOKORO_DIR,
    KROKO_DIR,
    LICENSES,
    MANIFEST,
    SHERPA_DIR,
    SUPERTONIC_DIR,
    ModelFile,
    in_dirs,
    required_dirs,
    sha256_of,
    verify,
    verify_all,
)

__all__ = ["KOKORO_DIR", "KROKO_DIR", "SHERPA_DIR", "SUPERTONIC_DIR", "verify_all"]


def download(models: Path, entry: ModelFile) -> str | None:
    path = models / entry.path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with urllib.request.urlopen(entry.url, timeout=60) as resp, part.open("wb") as f:
            while block := resp.read(1 << 20):
                f.write(block)
    except OSError as exc:
        part.unlink(missing_ok=True)
        return f"{entry.path}: download failed ({type(exc).__name__})"
    if sha256_of(part) != entry.sha256:
        part.unlink()
        return f"{entry.path}: checksum mismatch"
    part.replace(path)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_laptop_models")
    ap.add_argument("--models", type=Path, default=Path("models"))
    ap.add_argument("--check", action="store_true", help="verify only, never download")
    ap.add_argument(
        "--server-only",
        action="store_true",
        help="only what `python -m lan_speech` needs (Kroko + Supertonic); skip the rest",
    )
    args = ap.parse_args(argv)

    wanted = required_dirs("kroko") if args.server_only else None
    for name, lic in LICENSES.items():
        if wanted is None or name in wanted:
            print(f"license {name}: {lic}")
    problems = []
    for entry in MANIFEST:
        if not in_dirs(entry.path, wanted):
            continue
        problem = verify(args.models, entry)
        if problem and not args.check:
            print(f"fetching {entry.path}")
            problem = download(args.models, entry)
        if problem:
            problems.append(problem)
        else:
            print(f"ok {entry.path}")
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    return 2 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
