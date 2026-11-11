"""What the owner wrote about themselves (config/profile.md), answered locally with no model.

"What's my name?", "where do I work?", "tell me about myself" are answered by the fast path from the
profile's own fields: the text never goes to a cloud model (it did through a delegate job, which
was slow, cost a little and spoke minutes later). Nothing here is logged except the answer the
server speaks. No profile, or a field the profile lacks: no answer, and the turn goes on as before.
Links and email addresses are never read out.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

FIELDS: dict[str, str] = {}
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FIELD = re.compile(r"^\s*[-*]\s*([^:]{1,60}):\s*(\S.*)$")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def parse(text: str) -> dict[str, str]:
    """label (lower case) -> value, the first of a label wins; markdown links keep their text."""
    found: dict[str, str] = {}
    for line in _COMMENT.sub("", text).splitlines():
        match = _FIELD.match(line)
        if match is None:
            continue
        label, value = match.group(1).strip().lower(), _LINK.sub(r"\1", match.group(2)).strip()
        if value and not value.lower().startswith(("http", "mailto")) and "@" not in value:
            found.setdefault(label, value)
    return found


def load(path: Path) -> int:
    """Read the profile into FIELDS; how many fields were found (0: none, nothing is answered)."""
    FIELDS.clear()
    with contextlib.suppress(OSError):
        FIELDS.update(parse(path.read_text()))
    return len(FIELDS)


def _find(*labels: str) -> str | None:
    """The value of the first label that is one of `labels`, else one that starts with one."""
    for exact in (True, False):
        for label in labels:
            for key, value in FIELDS.items():
                if (key == label) if exact else (key.startswith(label) and key.split()[0] == label):
                    return value
    return None


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _work() -> str | None:
    job = _find("occupation", "role", "job", "title", "position")
    employer = _find("employer", "company", "organization", "organisation")
    if job and employer:
        return f"You work as {_article(job)} {job} at {employer}."
    if job:
        return f"You work as {_article(job)} {job}."
    return f"You work at {employer}." if employer else None


def _study() -> str | None:
    program = _find("degree program", "degree", "program", "major")
    school = _find("school", "university", "college")
    if program and school:
        return f"Your degree program is {program}, at {school}."
    if program:
        return f"Your degree program is {program}."
    return f"You study at {school}." if school else None


def _course() -> str | None:
    course = _find("current course name", "current course", "course")
    teacher = _find("instructor name", "instructor", "professor", "teacher")
    if course and teacher:
        return f"You're taking {course} with {teacher}."
    return f"You're taking {course}." if course else None


def owner_name() -> str | None:
    """The owner's name from the profile, for the one prompt line that lets the model greet them
    ("greet me by my name" tried to read PROFILE.md through open_app, 2026-11-11)."""
    return _find("name", "full name")


def answer(what: str) -> str | None:
    """The spoken answer for a kind of question, or None when the profile does not say."""
    name = _find("name", "full name")
    if what == "name":
        return f"Your name is {name}." if name else None
    if what == "work":
        return _work()
    if what == "study":
        return _study()
    if what == "city":
        city = _find("city", "location")
        return f"You're based in {city}." if city else None
    if what == "course":
        return _course()
    if what == "about":
        parts = [f"You're {name}." if name else None, _work(), _study()]
        return " ".join(p for p in parts if p) or None
    return None
