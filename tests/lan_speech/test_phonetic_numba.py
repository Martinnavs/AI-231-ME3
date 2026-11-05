"""The numba name search gives the numbers of the pure-Python one; without numba that path runs."""

from __future__ import annotations

import os
import random
import string
import subprocess
import sys
from pathlib import Path

import numpy as np

from lan_speech import phonetic_numba
from lan_speech.hotwords import load_entities
from lan_speech.transcript_repair import (
    _GAP,
    _SUB,
    Entity,
    EntityMatcher,
    _codes,
    phonemes,
    phonetic_distance,
)

ROOT = Path(__file__).resolve().parents[2]
LETTERS = string.ascii_lowercase + "SC"


def python_nearest(matcher: EntityMatcher, span: str, limit: int = 2) -> list[tuple[float, Entity]]:
    """The original loop of ``EntityMatcher.nearest``, kept here as the reference."""
    sound = phonemes(span)
    if len(sound) < 3:
        return []
    scored = []
    for ent, es in zip(matcher.entities, matcher._sounds, strict=True):
        if abs(len(es) - len(sound)) > max(1, int(0.25 * max(len(es), len(sound)))):
            continue
        scored.append((phonetic_distance(sound, es), ent))
    scored.sort(key=lambda t: t[0])
    return scored[:limit]


def random_word(rng: random.Random, low: int = 1, high: int = 14) -> str:
    return "".join(rng.choice(LETTERS) for _ in range(rng.randint(low, high)))


def kernel(sound: str, names: list[str], fn=None) -> np.ndarray:  # type: ignore[no-untyped-def]
    codes = _codes("".join(names))
    offsets = np.cumsum([0, *map(len, names)]).astype(np.int64)
    out = np.empty(len(names))
    (fn or phonetic_numba.distances)(_codes(sound), codes, offsets, _SUB, _GAP, out)
    return out


def test_kernel_matches_phonetic_distance_on_random_strings() -> None:
    rng = random.Random(7)
    for _ in range(300):
        sound = random_word(rng, 3, 12)
        names = [random_word(rng, 1, 14) for _ in range(40)]
        for fn in (phonetic_numba.distances, phonetic_numba.distances_py):
            got = kernel(sound, names, fn)
            for name, d in zip(names, got, strict=True):
                longest = max(len(sound), len(name))
                if abs(len(name) - len(sound)) > max(1, int(0.25 * longest)):
                    assert d == -1.0
                else:
                    assert abs(d - phonetic_distance(sound, name)) < 1e-9


def test_nearest_equals_the_python_loop_on_random_spans() -> None:
    rng = random.Random(11)
    matcher = EntityMatcher(
        [Entity(random_word(rng, 3, 14).replace("S", "sh")) for _ in range(300)]
    )
    for _ in range(300):
        span = " ".join(random_word(rng, 2, 8).lower() for _ in range(rng.randint(1, 3)))
        for limit in (1, 2, 5):
            assert matcher.nearest(span, limit) == python_nearest(matcher, span, limit)


def test_nearest_equals_the_python_loop_on_the_real_names() -> None:
    entities = load_entities(ROOT / "config/entities.json")
    matcher = EntityMatcher(entities)
    spans = ["dumagatete", "john grady cole", "marisol", "pie", "bini", "opalate", "turn on lights",
             "good morning", "taylor swift", "pantropiko", "davao"]  # fmt: skip
    for span in spans:
        assert matcher.nearest(span) == python_nearest(matcher, span)


def test_fallback_runs_without_numba() -> None:
    code = (
        "from lan_speech import phonetic_numba as p, transcript_repair as t\n"
        "assert not p.AVAILABLE\n"
        "m = t.EntityMatcher([t.Entity('Opalite'), t.Entity('Pantropiko')])\n"
        "t.warm_up()\n"
        "print(m.nearest('opalate')[0][1].name)\n"
    )
    env = {**os.environ, "LAN_SPEECH_NO_NUMBA": "1"}
    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "Opalite"


def test_warm_up_can_be_called_twice() -> None:
    phonetic_numba.warm_up()
    phonetic_numba.warm_up()
