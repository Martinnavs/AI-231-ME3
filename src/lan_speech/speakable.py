"""Reply text made speakable, for every TTS engine: what a person would say, not what is written.

Found in a live session (2026-11-11): "edu.ph" was read as "edu <pause> ph", "UP" as the word
"up", a file name such as email_to_upd_professor.md as a run of fragments, and markdown marks
(# and **) were spoken.
"""

from __future__ import annotations

import re

FILE_WORDS = {
    "md": "markdown file",
    "txt": "text file",
    "py": "python file",
    "csv": "C S V file",
    "html": "H T M L file",
    "json": "J S O N file",
    "pdf": "P D F file",
    "js": "javascript file",
    "css": "C S S file",
}
TLDS = "com|org|net|edu|gov|ph|io|ai|co|uk|dev|app|info|us|jp|au|ca|de"
SPELLED = {"UP", "UPD", "UPV", "UPLB", "UPOU", "USC", "UST", "DOST", "CHED"}  # letters, not a word

_URL = re.compile(r"\b(?:https?://)?(?:www\.)?((?:[A-Za-z0-9-]+\.)+(?:" + TLDS + r"))\b(?:/\S*)?")
_FILE = re.compile(r"\b([A-Za-z0-9][\w-]*)\.(" + "|".join(FILE_WORDS) + r")\b")
_INITIALS = re.compile(r"\b(?:[A-Za-z]\.){2,}")
_PHD = re.compile(r"\bPh\.?D\.?")
_ACRONYM = re.compile(r"\b(" + "|".join(sorted(SPELLED, key=len, reverse=True)) + r")\b")
_MARK = re.compile(
    r"(?m)^\s{0,3}#{1,6}\s*|^\s*(?:[-*•]|\d{1,2}[.)])\s+|[*_`]{1,3}(?=\S)|(?<=\S)[*`]{1,3}"
)


def _file(match: re.Match[str]) -> str:
    return f"{re.sub(r'[_-]+', ' ', match[1])} {FILE_WORDS[match[2].lower()]}"


def speakable(text: str) -> str:
    """The text with web addresses, file names, dotted initials, spelled acronyms and markdown
    marks put in the form a voice should say."""
    text = _FILE.sub(_file, text)
    text = _URL.sub(lambda m: m[1].replace(".", " dot "), text)
    text = re.sub(r"\be\.g\.,?", "for example", text, flags=re.I)
    text = re.sub(r"\bi\.e\.,?", "that is", text, flags=re.I)
    text = _PHD.sub("P H D", text)
    text = _INITIALS.sub(lambda m: " ".join(m[0].replace(".", "")), text)
    text = _ACRONYM.sub(lambda m: " ".join(m[1]), text)
    text = _MARK.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text)
