"""Random but readable sentences, for testing speech without a language model.

The same seed always gives the same sentences, so a run can be repeated and what the recogniser
heard can be compared word for word with what was said. The vocabulary is plain English (no names
the recogniser has to guess) with some variety in length and shape.
"""

from __future__ import annotations

import random

_PEOPLE = [
    "the baker",
    "my neighbour",
    "a young pilot",
    "the old teacher",
    "our guide",
    "the nurse",
]
_THINGS = [
    "lantern",
    "bicycle",
    "umbrella",
    "notebook",
    "violin",
    "kettle",
    "window",
    "garden",
    "bridge",
    "ladder",
    "basket",
    "candle",
]
_ADJ = [
    "small",
    "bright",
    "heavy",
    "quiet",
    "broken",
    "wooden",
    "golden",
    "narrow",
    "cold",
    "ancient",
]
_PLACES = [
    "by the river",
    "in the market",
    "near the station",
    "behind the school",
    "under the stairs",
    "across the field",
]
_VERBS_PAST = [
    "carried",
    "found",
    "painted",
    "repaired",
    "forgot",
    "borrowed",
    "polished",
    "followed",
]
_VERBS = ["carry", "find", "paint", "repair", "borrow", "polish", "follow", "open"]
_WHEN = [
    "this morning",
    "last night",
    "on Tuesday",
    "before sunrise",
    "after the storm",
    "every winter",
]
_NUMBERS = ["two", "three", "five", "seven", "twelve", "twenty"]

_TEMPLATES = [
    "{person} {past} the {adj} {thing} {place} {when}.",
    "{when} {person} {past} {number} {adj} {thing}s.",
    "Please {verb} the {adj} {thing} {place}.",
    "Can you {verb} the {thing} {place} {when}?",
    "What happened to the {adj} {thing} {place}?",
    "How many {thing}s did {person} {verb} {when}?",
    "There was a {adj} {thing} {place}, and {person} {past} it.",
    "Where did {person} {verb} the {thing}?",
]


def sentence(rng: random.Random) -> str:
    text = rng.choice(_TEMPLATES).format(
        person=rng.choice(_PEOPLE),
        past=rng.choice(_VERBS_PAST),
        verb=rng.choice(_VERBS),
        adj=rng.choice(_ADJ),
        thing=rng.choice(_THINGS),
        place=rng.choice(_PLACES),
        when=rng.choice(_WHEN),
        number=rng.choice(_NUMBERS),
    )
    return text[0].upper() + text[1:]


def phrases(seed: int | str, count: int = 1) -> list[str]:
    """``count`` sentences that depend only on ``seed``."""
    rng = random.Random(str(seed))
    return [sentence(rng) for _ in range(count)]


def split_in_two(text: str, rng: random.Random | None = None) -> tuple[str, str]:
    """Cut a sentence at a word boundary near its middle (a speaker who pauses mid-sentence)."""
    words = text.split()
    cut = max(2, min(len(words) - 2, len(words) // 2 + (rng.choice([-1, 0, 1]) if rng else 0)))
    return " ".join(words[:cut]), " ".join(words[cut:])
