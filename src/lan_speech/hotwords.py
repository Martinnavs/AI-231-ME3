"""Files sherpa-onnx needs to bias Parakeet toward a list of names, and the list loader.

sherpa encodes hotwords with a sentencepiece-style vocabulary; Parakeet ships only
``tokens.txt``, so a ``piece<TAB>score`` file is derived from it (rank-based scores). Each name
is written in lower and Title case because the model's vocabulary is cased. Measured effect and
caveats: docs/STT-HOSTING.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from lan_speech.transcript_repair import Entity


def write_vocab(tokens: Path, out: Path) -> None:
    """Write the 'piece<TAB>score' file sherpa needs to encode hotwords (rank-based scores)."""
    with out.open("w", encoding="utf-8") as f:
        for line in tokens.read_text(encoding="utf-8").splitlines():
            piece, idx = line.rsplit(" ", 1)
            f.write(f"{piece}\t{-float(idx) / 100:.4f}\n")


def write_hotwords(names: list[str], score: float, out: Path) -> None:
    """One ``name :score`` line per spelling; the vocabulary is cased, so lower and Title."""
    lines = sorted({f"{form} :{score}" for n in names for form in (n.lower(), n.title())})
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_entities(path: Path) -> list[Entity]:
    """Names to bias toward and repair against.

    JSON: either ``{"Taylor Swift": "music", ...}`` (name -> category) or a plain list of names.
    Categories pick which list the repair step searches (contact, music, place, brand).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [Entity(str(n), str(c)) for n, c in data.items()]
    return [Entity(str(n)) for n in data]
