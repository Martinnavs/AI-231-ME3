"""What the listener hears: sentences the assistant must not say (2026-11-12, two live sessions).

Three rules on the sentences of one reply, from the audit of the agent server's logs
(`scripts/output_audit.py` uses the same rules):

* a first-person status sentence that restates the one before it ("I am updating the website ...
  OK, I am updating the website with those holidays ...") is dropped;
* in a turn that only STARTED a job, a completion claim ("I have updated ...") is dropped: the job
  does not exist yet as a result;
* a backchannel or an apology on its own ("Mm-hmm.", "I'm sorry.") is not a request.

No model, no network. `SentenceFilter` works on the stream: it holds a piece until its sentence is
complete (the client speaks sentence by sentence anyway) and lets the complete ones through.
"""

from __future__ import annotations

import re
from collections.abc import Callable

OVERLAP = 0.34  # share of the shorter sentence's content words that the other has
MIN_WORDS = 3  # content words a sentence needs to be compared
_STOP_TEXT = (
    "a an the and or of to in on for with is are was were be i you it this that my your we am "
    "will would can could have has had do does did not no yes ok okay now then just also so as at "
    "by from up out about into over its me us them they he she there here what which who when"
)
STOP = frozenset(re.findall(r"[a-z]+", _STOP_TEXT))
# a sentence in the assistant's own voice about its own action ("I am updating ...", "OK, I am
# ...", "It's currently being processed ..."); a sentence that names the topic again before its
# answer ("Looking up X. X was ...") is natural and is not one of these
AGENT_VOICE = re.compile(
    r"^(?:ok(?:ay)?[,.]?\s+|sure[,.]?\s+|alright[,.]?\s+)?(?:i(?:'m|'ve| am| have| will|'ll)\b"
    r"|it(?:'s| is) (?:currently|being)|the (?:task|update|job|website|page)\b)",
    re.IGNORECASE,
)
PREMATURE = re.compile(
    r"\bI(?:'ve| have)\s+(?:just\s+)?(?:updated|added|created|built|finished|made|changed)\b",
    re.IGNORECASE,
)
_SPLIT = re.compile(r"(?<=[.!?])\s+")
# not a request: a listener's sound, or an apology said on its own (often the first words of a
# restatement that the speech endpoint cut off)
_BACKCHANNEL = re.compile(
    r"^\W*(?:mm+[- ]?hm+|uh[- ]?huh|hmm+|mhm|uh|um|ah|oh|i see)\W*$", re.IGNORECASE
)
_APOLOGY = re.compile(r"^\W*(?:i'?m\s+)?(?:so\s+)?sorry\W*$|^\W*oops\W*$", re.IGNORECASE)


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 2}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text.strip()) if s.strip()]


def restates(a: str, b: str) -> bool:
    """Two sentences share most of the content words of the shorter one."""
    wa, wb = content_words(a), content_words(b)
    small = min(len(wa), len(wb))
    return small >= MIN_WORDS and len(wa & wb) / small >= OVERLAP


def repeated(text: str) -> list[tuple[str, str]]:
    """Pairs of first-person status sentences of one reply that restate each other."""
    parts = [p for p in sentences(text) if AGENT_VOICE.match(p)]
    return [(a, b) for i, a in enumerate(parts) for b in parts[i + 1 :] if restates(a, b)]


def is_backchannel(text: str) -> bool:
    return _BACKCHANNEL.match(text) is not None


def is_apology(text: str) -> bool:
    return _APOLOGY.match(text) is not None


# first words of a sentence that may be the assistant talking about its own action: only such a
# sentence is held until it is complete; any other sentence flows through as it streams
_OPENERS = frozenset(
    {"i", "i'm", "i've", "i'll", "ok", "okay", "sure", "alright", "it's", "it", "the"}
)
_FIRST_WORD = re.compile(r"\s*([A-Za-z']+)[\s,.!?:;]")
_END = re.compile(r"[.!?]\s")


class SentenceFilter:
    """Streaming filter for the text of one reply; `job_started()` says whether this turn started
    a background job. `note` records a sentence spoken another way (the `say` sentence). A
    sentence that opens like a status sentence is held until it ends and checked; the others
    pass at once, so the stream keeps its pace."""

    def __init__(self, job_started: Callable[[], bool] = lambda: False) -> None:
        self.job_started = job_started
        self.held = ""  # the start of the current sentence, while it could be a status sentence
        self.passing = False  # the current sentence was cleared and flows through
        self.last = ""  # the last character let through (a boundary can span two pieces)
        self.said: list[str] = []
        self.dropped: list[str] = []

    def note(self, text: str) -> None:
        self.said += [s for s in sentences(text) if AGENT_VOICE.match(s)]

    def _keep(self, sentence: str) -> bool:
        if self.job_started() and PREMATURE.search(sentence):
            return False
        if AGENT_VOICE.match(sentence):
            if any(restates(sentence, earlier) for earlier in self.said):
                return False
            self.said.append(sentence)
        return True

    def _check(self, parts: list[str]) -> str:
        kept = []
        for sentence in parts:
            if self._keep(sentence):
                kept.append(sentence)
            else:
                self.dropped.append(sentence)
        return "".join(s + " " for s in kept)

    def _clears(self, partial: str) -> bool:
        """The sentence has begun with a word that cannot start a status sentence."""
        first = _FIRST_WORD.match(partial)
        return first is not None and first[1].lower() not in _OPENERS

    def feed(self, text: str) -> str:
        out: list[str] = []
        while text:
            if self.passing:
                seen = self.last + text
                found = _END.search(seen)
                if found is None:
                    out.append(text)
                    self.last, text = text[-1], ""
                else:
                    cut = found.end() - len(self.last)
                    out.append(text[:cut])
                    self.last, text, self.passing = text[cut - 1], text[cut:], False
                continue
            self.held += text
            text = ""
            parts = _SPLIT.split(self.held)
            if len(parts) > 1:
                self.held = parts.pop()
                out.append(self._check([p.strip() for p in parts if p.strip()]))
                self.last = " "
            if self.held and self._clears(self.held):
                out.append(self.held)
                self.last, self.held, self.passing = self.held[-1], "", True
        return "".join(out)

    def finish(self) -> str:
        held, self.held, self.passing = self.held.strip(), "", False
        return self._check([held]) if held else ""
