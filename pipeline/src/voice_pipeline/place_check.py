"""Is a place the router passed to `weather` a name we know? (W4)

Uses the speech server's repair matcher (lan_speech.transcript_repair.EntityMatcher, with its
numba kernel) over every name in config/entities.json. A place within NEAR_MAX of a name is
"known": the same bar family as the repair ladder (silent replacement at 0.16)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ENTITIES = ROOT / "config" / "entities.json"
NEAR_MAX = 0.10
SKIP_WORDS = frozenset({"city", "province", "island", "the", "of", "de", "del"})
_matcher: Any = None
_failed = False


def _load() -> Any:
    global _matcher, _failed
    if _matcher is None and not _failed:
        try:
            if str(ROOT / "src") not in sys.path:
                sys.path.insert(0, str(ROOT / "src"))
            from lan_speech.transcript_repair import (  # type: ignore[import-not-found]
                Entity,
                EntityMatcher,
            )

            names = json.loads(ENTITIES.read_text())
            _matcher = EntityMatcher([Entity(name, "name") for name in names])
        except Exception:  # no matcher: the router keeps the turn (it did before this rule)
            _failed = True
    return _matcher


def near(text: str) -> bool:
    matcher = _load()
    if matcher is None:
        return True
    hit = matcher.nearest(text, 1)
    return bool(hit) and hit[0][0] <= NEAR_MAX


def known_place(place: str) -> bool:
    """Empty (home), the whole string, or any word of 4+ letters is near a known name; strings
    too short for the matcher (under 3 sound letters) are left to the router."""
    place = place.strip()
    if not place or len(re.sub(r"[^a-z]", "", place.lower())) < 3 or _load() is None:
        return True
    if near(place):
        return True
    words = [w for w in re.findall(r"[A-Za-z']+", place) if len(w) >= 4]
    return any(near(w) for w in words if w.lower() not in SKIP_WORDS)
