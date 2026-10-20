"""Repair named entities in an STT transcript against a known list, and decide when to ask.

STT gets most words right but mangles names (``opalate`` for Opalite, ``Binnie`` for BINI).
The agent usually holds a list of names it could have been told about: contacts, the music
library, device names, places. This module finds spans of the transcript that *sound like* a
listed name and applies a repair ladder:

1. SILENT    close match, clear winner: replace it quietly.
2. IMPLICIT  fairly close, low-risk action: replace it and let the reply echo the name.
3. CONFIRM   plausible but not safe to assume (or a risky action): ask "Did you mean X?".
4. TARGETED  the action needs a name of some kind and none was found: ask for just that slot.
5. REPROMPT  nothing usable: "Sorry, I didn't catch that."

Matching is phonetic (a small rule-based grapheme-to-sound pass, then a weighted edit distance
that treats b/p, d/t, f/p, vowels etc. as cheap substitutions), pure Python with no
dependencies. Thresholds live in :class:`RepairConfig` and are meant to be tuned on recordings
(``scripts/stt_repair_eval.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum


class Risk(Enum):
    """How costly it is if the repaired name is wrong (play a song vs place a call)."""

    LOW = "low"
    HIGH = "high"


class Rung(IntEnum):
    NONE = 0
    SILENT = 1
    IMPLICIT = 2
    CONFIRM = 3
    TARGETED = 4
    REPROMPT = 5


@dataclass(frozen=True)
class Entity:
    name: str
    category: str = ""


@dataclass(frozen=True)
class Word:
    text: str
    conf: float | None = None  # decoder confidence in [0, 1] when available


@dataclass(frozen=True)
class RepairConfig:
    silent_max: float = 0.16  # distance at or below which a clear winner is replaced silently
    implicit_max: float = 0.26  # ... replaced with an echo, low-risk actions only
    confirm_max: float = 0.30  # ... offered as "did you mean" (weaker guesses are not offered)
    same_sound_max: float = 0.03  # an ordinary word this close to a name is that name respelled
    high_risk_silent_max: float = 0.08  # silent-replace bar when the action is risky
    margin_min: float = 0.06  # best must beat the runner-up entity by this much to act alone
    min_letters: int = 4  # shortest span (letters) we will try to repair
    max_span_words: int = 4
    confident: float = 0.97  # a span whose words are all at least this confident is left alone
    reprompt_conf: float = 0.35  # mean confidence below which the turn is not understood


@dataclass(frozen=True)
class Match:
    start: int  # word index, inclusive
    end: int  # word index, exclusive
    heard: str
    entity: Entity
    distance: float
    margin: float
    runner_up: Entity | None
    rung: Rung


@dataclass(frozen=True)
class Repair:
    text: str  # transcript with SILENT and IMPLICIT replacements applied
    rung: Rung  # the most intrusive rung any part of the turn needed
    matches: tuple[Match, ...] = ()
    question: str | None = None  # what to say instead of acting (CONFIRM, TARGETED, REPROMPT)
    echo: tuple[str, ...] = field(default_factory=tuple)  # names to repeat back (IMPLICIT)


# ---------------------------------------------------------------- phonetics

_RULES: tuple[tuple[str, str], ...] = (
    ("chr", "kr"),
    ("tch", "C"),
    ("ch", "C"),
    ("sh", "S"),
    ("ph", "f"),
    ("th", "t"),
    ("ck", "k"),
    ("qu", "kw"),
    ("q", "k"),
    ("x", "ks"),
    ("dg", "j"),
    ("gh", ""),
    ("wh", "w"),
    ("kn", "n"),
    ("wr", "r"),
    ("c(?=[eiy])", "s"),
    ("c", "k"),
    ("z", "s"),
    ("(.)\\1", "\\1"),
)
_VOWELS = frozenset("aeiou")
_CLOSE = (
    "bp",
    "dt",
    "kg",
    "fp",
    "vb",
    "sz",
    "jC",
    "jg",
    "sS",
    "SC",
    "nm",
    "lr",
    "fv",
)
_CLOSE_PAIRS = frozenset(p for pair in _CLOSE for p in (pair, pair[::-1]))


def phonemes(text: str) -> str:
    """Rough sound-alike string: letters only, digraphs folded, doubled letters merged."""
    s = re.sub(r"[^a-z]", "", text.lower())
    for pattern, repl in _RULES:
        s = re.sub(pattern, repl, s)
    s = re.sub(r"(?<=[aeiou])w", "u", s)
    s = re.sub(r"(?<=.)h", "", s)  # a non-initial h is almost always silent
    s = re.sub(r"(?<=[^aeiou])y|y$", "i", s)
    return re.sub(r"(.)\1", r"\1", s)


def _sub(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if a in _VOWELS and b in _VOWELS:
        return 0.3
    if a + b in _CLOSE_PAIRS:
        return 0.45
    if (a in _VOWELS) != (b in _VOWELS):
        return 1.0
    return 1.0


def _gap(ch: str) -> float:
    if ch in _VOWELS:
        return 0.5
    return 0.3 if ch in "hwy" else 1.0


def phonetic_distance(a: str, b: str) -> float:
    """Weighted edit distance between two phoneme strings, scaled to 0 (same) .. ~1."""
    if not a or not b:
        return 1.0
    prev = [0.0]
    for ch in b:
        prev.append(prev[-1] + _gap(ch))
    for ca in a:
        cur = [prev[0] + _gap(ca)]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + _gap(ca), cur[j - 1] + _gap(cb), prev[j - 1] + _sub(ca, cb)))
        prev = cur
    return prev[-1] / max(len(a), len(b))


# ---------------------------------------------------------------- matching

COMMON_WORDS = frozenset(
    """a about after again all also am an and any are as at back be because been before being
    between big but by call came can come could day did do does don't down each even every
    find first for from get give go going good got great had has have he her here him his how
    i if in into is it its just know large last like little long look made make man many may me
    more most much must my need never new no not now of off on one only or other our out over
    own part people play put read right said same say see she should show small so some still
    such take tell than that the their them then there these they thing think this those three
    through time to too two under up us use very want was water way we well went were what when
    where which while who why will with word work would write year yes you your music song
    songs stop turn light lights set timer alarm call text send message remind reminder weather
    please open close next then play pause volume louder quieter tomorrow today tonight morning
    evening minutes minute hour hours seconds second temperature degrees saying says talk
    talking telling ask asking hello hi hey thanks thank okay ok bye goodbye happy birthday
    dinner lunch breakfast late early
    soon home house school food eat drink buy order bring let lets going gonna wanna maybe really
    something anything nothing everything someone anyone nobody everyone always sometimes
    already actually probably later until during
    """.split()  # noqa: SIM905
)


def _norm_words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", text.lower().replace("’", "'")).split()


class EntityMatcher:
    """Holds the known names and finds the closest-sounding one for a span of words."""

    def __init__(
        self, entities: list[Entity], *, known_words: frozenset[str] = frozenset()
    ) -> None:
        """``known_words``: a dictionary (e.g. CMUdict); spans made only of these are left alone."""
        self._common = COMMON_WORDS | known_words
        self.known = known_words
        self._entities = entities
        self._sounds = [phonemes(e.name) for e in entities]
        self._exact = {" ".join(_norm_words(e.name)) for e in entities}

    @property
    def entities(self) -> list[Entity]:
        return self._entities

    def is_exact(self, span: str) -> bool:
        return " ".join(_norm_words(span)) in self._exact

    def nearest(self, span: str, limit: int = 2) -> list[tuple[float, Entity]]:
        """Closest entities to ``span`` by sound, best first (distance, entity)."""
        sound = phonemes(span)
        if len(sound) < 3:
            return []
        scored: list[tuple[float, Entity]] = []
        for ent, es in zip(self._entities, self._sounds, strict=True):
            if abs(len(es) - len(sound)) > max(1, int(0.25 * max(len(es), len(sound)))):
                continue
            scored.append((phonetic_distance(sound, es), ent))
        scored.sort(key=lambda t: t[0])
        return scored[:limit]

    def all_common(self, words: list[str]) -> bool:
        return all(w in self._common for w in words)

    def is_common(self, word: str) -> bool:
        return word in self._common

    def has_category(self, text: str, category: str) -> bool:
        """Whether ``text`` already contains a listed name of this category."""
        norm = " " + " ".join(_norm_words(text)) + " "
        return any(
            e.category == category and f" {' '.join(_norm_words(e.name))} " in norm
            for e in self._entities
        )


SLOT_NOUN = {
    "music": "artist or song",
    "contact": "contact",
    "place": "place",
    "brand": "brand or shop",
}


def _rung_for(d: float, margin: float, risk: Risk, cfg: RepairConfig) -> Rung:
    clear = margin >= cfg.margin_min
    silent_max = cfg.silent_max if risk is Risk.LOW else cfg.high_risk_silent_max
    if d <= silent_max and clear:
        return Rung.SILENT
    if d <= cfg.implicit_max and clear and risk is Risk.LOW:
        return Rung.IMPLICIT
    if d <= cfg.confirm_max:
        return Rung.CONFIRM
    return Rung.NONE


def _edges_belong(flat: list[str], ent: Entity, matcher: EntityMatcher) -> bool:
    """A leading or trailing ordinary word ("call" in "call rowana") must sound like the name's.

    Names do start with ordinary words ("The Weeknd", "Up Dharma Down"), so compare against the
    name's own first and last word instead of banning them.
    """
    if len(flat) < 2:
        return True
    words = _norm_words(ent.name)
    if not words:
        return True
    for heard, name_word in ((flat[0], words[0]), (flat[-1], words[-1])):
        if matcher.is_common(heard) and (
            phonetic_distance(phonemes(heard), phonemes(name_word)) > 0.35
        ):
            return False
    return True


def _exact_coverage(tokens: list[list[str]], matcher: EntityMatcher, max_words: int) -> set[int]:
    """Word positions that are already (part of) a listed name; longest names claim first."""
    covered: set[int] = set()
    for i in range(len(tokens)):
        for n in range(max_words, 0, -1):
            if i + n > len(tokens):
                continue
            flat = [t for ws in tokens[i : i + n] for t in ws]
            if matcher.is_exact(" ".join(flat)):
                covered.update(range(i, i + n))
                break
    return covered


def _contains_exact(flat: list[str], matcher: EntityMatcher) -> bool:
    """True if a strictly shorter run of these words is already a listed name."""
    n = len(flat)
    return any(
        matcher.is_exact(" ".join(flat[a:b]))
        for a in range(n)
        for b in range(a + 1, n + 1)
        if b - a < n
    )


def find_matches(
    words: list[Word],
    matcher: EntityMatcher,
    risk: Risk = Risk.LOW,
    cfg: RepairConfig | None = None,
) -> list[Match]:
    """Non-overlapping repairs for the transcript, in order of position."""
    cfg = cfg or RepairConfig()
    tokens = [_norm_words(w.text) for w in words]
    covered = _exact_coverage(tokens, matcher, cfg.max_span_words)
    cands: list[Match] = []
    for i in range(len(words)):
        for n in range(1, cfg.max_span_words + 1):
            j = i + n
            if j > len(words):
                break
            if not covered.isdisjoint(range(i, j)):
                continue  # part of a name that is already right
            flat = [t for ws in tokens[i:j] for t in ws]
            span = " ".join(flat)
            if len(re.sub(r"[^a-z]", "", span)) < cfg.min_letters or matcher.is_exact(span):
                continue
            if _contains_exact(flat, matcher):
                continue
            ordinary = matcher.all_common(flat)
            confs = [w.conf for w in words[i:j]]
            if all(c is not None and c >= cfg.confident for c in confs):
                continue
            near = matcher.nearest(span)
            if not near:
                continue
            d, ent = near[0]
            if ordinary and d > cfg.same_sound_max:
                continue  # an ordinary word, not a mangled name
            runner = near[1] if len(near) > 1 else None
            margin = (runner[0] - d) if runner else 1.0
            if " ".join(_norm_words(ent.name)) == span:
                continue
            if not _edges_belong(flat, ent, matcher):
                continue
            rung = _rung_for(d, margin, risk, cfg)
            if rung is Rung.NONE:
                continue
            cands.append(Match(i, j, span, ent, d, margin, runner[1] if runner else None, rung))
    # greedy: lowest distance first, longer span wins ties, no overlaps
    cands.sort(key=lambda m: (m.distance, -(m.end - m.start)))
    chosen: list[Match] = []
    taken: set[int] = set()
    for m in cands:
        if taken.isdisjoint(range(m.start, m.end)):
            chosen.append(m)
            taken.update(range(m.start, m.end))
    return sorted(chosen, key=lambda m: m.start)


def repair(
    words: list[Word],
    matcher: EntityMatcher,
    *,
    risk: Risk = Risk.LOW,
    expected_category: str | None = None,
    cfg: RepairConfig | None = None,
) -> Repair:
    """Apply the repair ladder to a transcript given as words (with confidences if known)."""
    cfg = cfg or RepairConfig()
    if not words:
        return Repair("", Rung.REPROMPT, question="Sorry, I didn't catch that.")
    confs = [w.conf for w in words if w.conf is not None]
    if confs and sum(confs) / len(confs) < cfg.reprompt_conf:
        text = " ".join(w.text for w in words)
        return Repair(text, Rung.REPROMPT, question="Sorry, I didn't catch that.")

    matches = find_matches(words, matcher, risk, cfg)
    out: list[str] = []
    echo: list[str] = []
    pos = 0
    for m in matches:
        if m.rung in (Rung.SILENT, Rung.IMPLICIT):
            out.extend(w.text for w in words[pos : m.start])
            out.append(m.entity.name)
            pos = m.end
            if m.rung is Rung.IMPLICIT:
                echo.append(m.entity.name)
    out.extend(w.text for w in words[pos:])
    text = " ".join(out)

    confirm = [m for m in matches if m.rung is Rung.CONFIRM]
    if confirm:
        m = confirm[0]
        if m.runner_up and m.margin < cfg.margin_min:
            q = f"Did you mean {m.entity.name} or {m.runner_up.name}?"
        else:
            q = f"Did you mean {m.entity.name}?"
        return Repair(text, Rung.CONFIRM, tuple(matches), q, tuple(echo))

    if expected_category and not matcher.has_category(text, expected_category):
        return Repair(
            text,
            Rung.TARGETED,
            tuple(matches),
            f"Which {SLOT_NOUN.get(expected_category, expected_category)} was that?",
            tuple(echo),
        )
    rung = Rung.IMPLICIT if echo else (Rung.SILENT if matches else Rung.NONE)
    return Repair(text, rung, tuple(matches), None, tuple(echo))


# ---------------------------------------------------------------- using it on a transcript


def _wordset(text: str) -> frozenset[str]:
    return frozenset(text.split())


_FILLER = _wordset("please hey hi ok okay can could would will you i want to like just go ahead")
_CONTACT_VERBS = _wordset("call text message send email dial ring whatsapp dm")
_MUSIC_VERBS = _wordset("play queue shuffle listen")
_PLACE_CUES = _wordset(
    "weather rain raining temperature forecast navigate directions route nearest traffic"
)
MAX_COMMAND_WORDS = 14  # a longer utterance is a statement or a question, not a command
_BRAND_VERBS = _wordset("order buy book pay")
_PRONOUNS = _wordset("me it this that them us you him her myself yourself")


_SUFFIXES = frozenset({"", "s", "es", "d", "ed", "ing"})


def _is_verb(word: str, verbs: frozenset[str]) -> bool:
    """``word`` is one of ``verbs`` or a plain inflection of it (called, texting, plays)."""
    return any(word.startswith(v) and word[len(v) :] in _SUFFIXES for v in verbs)


def infer_context(text: str) -> tuple[str | None, Risk]:
    """Guess (slot, risk) from the sentence alone: "call ..." wants a contact and is risky.

    A slot may combine kinds with "+" ("brand+place" for "book a grab to Makati").
    A stand-in for the agent's own intent, for trying the system without an agent. A client that
    knows the intent should send it (``slot`` and ``risk`` on the start message) instead.
    """
    all_words = _norm_words(text)
    words = [w for w in all_words if w not in _FILLER]
    if not words or len(all_words) > MAX_COMMAND_WORDS:
        return None, Risk.LOW
    first = words[0]
    if _is_verb(first, _CONTACT_VERBS):
        if len(words) > 1 and words[1] in _PRONOUNS:
            return None, Risk.LOW  # "call me Ishmael", "text me", "send it": no name to look up
        return "contact", Risk.HIGH
    if _is_verb(first, _MUSIC_VERBS) or words[:2] == ["put", "on"]:
        return "music", Risk.LOW
    if _is_verb(first, _BRAND_VERBS):
        return "brand+place", Risk.LOW
    if _PLACE_CUES.intersection(words) or " ".join(words[:2]) == "how far":
        return "place", Risk.LOW
    return None, Risk.LOW


def build_matchers(
    entities: list[Entity], known_words: frozenset[str] = frozenset()
) -> dict[str, EntityMatcher]:
    """One matcher per entity category: repair only searches the names the agent expects."""
    cats = sorted({e.category for e in entities if e.category})
    return {
        c: EntityMatcher([e for e in entities if e.category == c], known_words=known_words)
        for c in cats
    }


def repair_text(
    text: str,
    matchers: dict[str, EntityMatcher],
    slot: str | None,
    risk: Risk,
    cfg: RepairConfig | None = None,
) -> Repair | None:
    """Run the ladder on a plain-text transcript. None when there is no list for this slot.

    ``slot`` may name several kinds joined with "+"; the names of all of them are searched
    together and no "which one was that?" question is asked.
    """
    kinds = [k for k in (slot or "").split("+") if k in matchers]
    if not kinds:
        return None
    if len(kinds) == 1:
        matcher = matchers[kinds[0]]
        expected: str | None = kinds[0]
    else:
        matcher = EntityMatcher(
            [e for k in kinds for e in matchers[k].entities], known_words=matchers[kinds[0]].known
        )
        expected = None
    return repair(
        [Word(w) for w in text.split()], matcher, risk=risk, expected_category=expected, cfg=cfg
    )


def repair_to_dict(r: Repair, slot: str | None, risk: Risk) -> dict[str, object]:
    """JSON-friendly summary of a repair for the protocol."""
    return {
        "rung": r.rung.name.lower(),
        "question": r.question,
        "echo": list(r.echo),
        "slot": slot,
        "risk": risk.value,
        "matches": [
            {
                "heard": m.heard,
                "entity": m.entity.name,
                "distance": round(m.distance, 3),
                "margin": round(m.margin, 3),
                "rung": m.rung.name.lower(),
            }
            for m in r.matches
        ],
    }
