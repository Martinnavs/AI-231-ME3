"""Was this turn meant for the assistant? Text and session only, no audio, no I/O.

Only "ignore" may drop a turn. When an addressed cue and a side cue disagree, or neither
fires, the answer is "unsure" and the turn is answered as before.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


FOLLOWUP_S = 8.0  # a reply this recent makes a very short turn an answer or follow-up
FOLLOWUP_WORDS = 4

# Opening verb of a command ("play some jazz", "look up ...").
IMPERATIVES = _words(
    "play pause stop resume turn switch set open close show tell give find search look read "
    "call text send remind translate define explain describe calculate convert add remove "
    "delete skip next cancel start launch mute unmute volume repeat louder quieter"
)
# Question word opening the turn.
WH_WORDS = _words("what what's who who's when where why how which whose")
# Things an assistant is asked about, anywhere in the turn (plural 's' is stripped).
DEVICE_NOUNS = _words(
    "light lamp music song volume timer alarm weather fan aircon temperature time date"
)
# Indirect requests: "I want ...", "I'd like ...". Checked before the first-person side rule.
REQUEST_PHRASES = (
    "i want",
    "i need",
    "i'd like",
    "i would like",
    "i wanna",
    "i'm looking for",
    "i was wondering",
    "do you",
    "are you",
    "is there",
    "does ",
)
# Hey/hi before a name.
GREETINGS = _words("hey hi hello oh yo")
# Kinship or address terms that, as the first token, mark talk to someone else.
ADDRESS_TERMS = _words(
    "dad mom mum mama papa bro sis roomie babe honey guys everyone buddy sweetie"
)
# Skipped when looking for a command or question opener ("Oh, what is ...").
LEAD_FILLERS = _words("oh well so okay ok hey hi and but now um uh hmm actually")
# Capitalised openers followed by a comma that are not names ("Well, ...", "Okay, ...").
DISCOURSE_WORDS = _words(
    "well oh no yes yeah yep nope okay ok so now then sure right hmm um uh actually also and "
    "but wait sorry thanks thank please maybe first next finally anyway alright good great "
    "fine hey hi hello yo ah huh interesting nice cool really gosh wow man damn ugh ha of like "
    "just quick look listen see yup"
)
# Subjects of a statement about the speaker or the room, not a request.
SIDE_SUBJECTS = _words(
    "i i'm i'll i've i'd we we're we'll he he's she she's they they're it it's that's this "
    "there there's the my your his her their our"
)

# A statement needs a verb-like word; bare noun phrases ("The Pulitzer Prize") are not side talk.
VERB_CUES = _words(
    "is are was were am be been has have had do does did will would can could should must "
    "just think know feel going gonna got getting want need ran said went came left"
)
SIDE_MIN_WORDS = 3  # shorter statements ("I see.", "I'm sorry.") are too often replies to us

_WORD = re.compile(r"[a-z0-9']+")
# Rule: Optional greeting, optional title, one capitalised name, then a comma ("Hey Jamie,").
_VOCATIVE = re.compile(
    r"^(?:(?:[Hh]ey|[Hh]i|[Hh]ello|[Oo]h|[Yy]o)[,\s]+)?(?:(?:[Dd]r|[Mm]rs?|[Mm]s|[Pp]rof)\.?\s+)?([A-Z][a-z']+),\s"
)
_CAN_YOU = re.compile(r"\b(?:can|could|would|will) you\b")


@dataclass(frozen=True)
class Verdict:
    label: str  # "addressed" | "ignore" | "unsure"
    reasons: tuple[str, ...] = ()


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _addressed_cues(
    low: str, words: list[str], since_reply_s: float | None, pending: bool, names: Collection[str]
) -> list[str]:
    cues: list[str] = []
    if pending:
        cues.append("pending")  # a clarification is waiting for this answer
    lowered = {n.strip().lower() for n in names if n.strip()}
    if lowered.intersection(words):
        cues.append("name")  # the assistant's name was said
    lead = list(words)
    while len(lead) > 1 and lead[0] in LEAD_FILLERS:
        lead.pop(0)
    opener = lead[0] if lead else ""
    if opener in IMPERATIVES:
        cues.append(f"imperative:{opener}")
    if _CAN_YOU.search(low):
        cues.append("can_you")  # "can/could/would/will you ..."
    if "please" in words:
        cues.append("please")
    if opener in WH_WORDS:
        cues.append(f"wh:{opener}")
    for w in words:
        if _singular(w) in DEVICE_NOUNS:
            cues.append(f"noun:{w}")  # a thing the assistant controls or reports
            break
    if any(low.startswith(p) for p in REQUEST_PHRASES):
        cues.append("request_phrase")
    if since_reply_s is not None and since_reply_s <= FOLLOWUP_S and len(words) <= FOLLOWUP_WORDS:
        cues.append("followup")  # short turn right after a reply
    return cues


def _side_cues(text: str, low: str, words: list[str], declaratives: bool) -> list[str]:
    cues: list[str] = []
    m = _VOCATIVE.match(text)
    if m and m.group(1).lower() not in DISCOURSE_WORDS:
        cues.append("vocative")  # "Jamie, ..." talks to a person
        rest = text[m.end() :].lower()
        if _addressed_cues(rest, _WORD.findall(rest), None, False, ()):
            return []  # "Mateo, play ..." could be us addressed by name: unsure
    else:
        first = words[1] if len(words) > 1 and words[0] in GREETINGS else (words or [""])[0]
        if first in ADDRESS_TERMS:
            cues.append("address_term")  # "Dad, ..." / "guys, ..."
    if (
        declaratives  # opt-in: about 1-2 % false rejects on real turns (ISSUES A1)
        and not cues
        and len(words) >= SIDE_MIN_WORDS
        and "?" not in text
        and words[0] in SIDE_SUBJECTS
        and any(
            w in VERB_CUES or w.endswith(("'s", "'m", "'re", "'ve", "'ll", "'d", "n't"))
            for w in words
        )
    ):
        cues.append("declarative")  # a statement about me/them/the room, not a request
    return cues


def decide(
    text: str,
    *,
    since_reply_s: float | None,
    pending: bool,
    names: Collection[str] = (),
    declaratives: bool = False,
) -> Verdict:
    text = text.strip()
    low = text.lower()
    words = _WORD.findall(low)
    addressed = _addressed_cues(low, words, since_reply_s, pending, names)
    side = [] if pending or "name" in addressed else _side_cues(text, low, words, declaratives)
    if addressed and side:
        return Verdict("unsure", tuple(addressed + side))
    if addressed:
        return Verdict("addressed", tuple(addressed))
    if side:
        return Verdict("ignore", tuple(side))
    return Verdict("unsure", ())
