"""Pull the proper names out of a folder of documents, to bias speech recognition toward them.

The speech server can be told a list of names (``--entities``): Parakeet is biased toward them and
a name that is heard almost right is repaired. For doc-chat the names worth listing are the ones
in the documents ("Dumbledore", "Order of the Phoenix"), so they are collected from the text.
A name is a run of capitalised words, joined across "of", "the", "de" and so on; a word at the
start of a sentence only counts when it is not an ordinary word.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from lan_speech.transcript_repair import COMMON_WORDS

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*|[.!?]+|[,;:()\"“”]")
_CONNECTORS = frozenset({"of", "the", "de", "la", "van", "von", "der"})
_SENTENCE_END = frozenset(".!?\n")


# ordinary words that are capitalised in running text without being names
def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


# function words that a capitalised run can start with ("The Boy", "In Harry") but are not names
_EDGE = _words(
    "i he she it we they you his her their its my our your this that these those there here "
    "however but and or so then when while after before although because since if though "
    "in on at by for with from to as of an a the one two three four five six seven eight nine "
    "ten first second third last next also still just only even both each every all any some "
    "many most more much such other another what which who whom whose how why where"
)
# words that are not a name on their own when they merely start a sentence
_NOT_NAMES = COMMON_WORDS | _EDGE


def _joins(word: str, previous: str) -> bool:
    """'of', 'de'... join two capitalised words; 'the' only after 'of' (Order of the Phoenix)."""
    return word in _CONNECTORS and (word != "the" or previous == "of")


def _clean(token: str) -> str:
    token = token.replace("’", "'")
    return token[:-2] if token.endswith("'s") else token


def _flatten(text: str) -> str:
    """Soft line wraps become spaces; blank lines, headings, list and table lines end a sentence."""
    text = re.sub(r"\n\s*\n", " . ", text)
    text = re.sub(r"^\s*(?:#+|[-*|]|\d+\.)\s*(.*)$", r". \1 .", text, flags=re.MULTILINE)
    return re.sub(r"\s*\n\s*", " ", text)


def extract_names(text: str, *, min_count: int = 2, max_words: int = 5) -> Counter[str]:
    """Names in ``text`` with how often each appears.

    A single word is kept only if it appears ``min_count`` times and at least once in the middle of
    a sentence (a capital at the start of a sentence proves nothing). Longer names are kept as is.
    """
    tokens = _TOKEN.findall(_flatten(text))
    runs: list[tuple[list[str], bool]] = []  # (words, started a sentence)
    current: list[str] = []
    started = False
    sentence_start = True

    def close() -> None:
        nonlocal current
        if current:
            runs.append((current, started))
            current = []

    for tok in tokens:
        if not tok[0].isalpha():  # punctuation or a line break ends the run
            close()
            if tok[0] in ".!?":
                sentence_start = True
            continue
        word = _clean(tok)
        if word[:1].isupper():
            if not current:
                started = sentence_start
            current.append(word)
        elif current and _joins(word.lower(), current[-1]):
            current.append(word.lower())
        else:
            close()
        sentence_start = False
    close()

    total: Counter[str] = Counter()
    mid: Counter[str] = Counter()
    for run, at_start in runs:
        while run and run[-1] in _CONNECTORS:
            run = run[:-1]
        while run and run[0].lower() in _EDGE:  # "The Boy" -> "Boy", "In Harry" -> "Harry"
            run = run[1:]
            at_start = False
        if not run or len(run) > max_words or (len(run) == 1 and run[0].lower() in _NOT_NAMES):
            continue
        name = " ".join(run)
        total[name] += 1
        if not at_start:
            mid[name] += 1
    return Counter({n: c for n, c in total.items() if " " in n or (c >= min_count and mid[n] >= 1)})


def names_from_folder(folder: Path | str, *, min_count: int = 2, limit: int = 400) -> list[str]:
    """Most frequent names across every .md and .txt file under ``folder``."""
    text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(Path(folder).rglob("*"))
        if p.suffix in (".md", ".txt") and p.is_file()
    )
    return [n for n, _ in extract_names(text, min_count=min_count).most_common(limit)]
