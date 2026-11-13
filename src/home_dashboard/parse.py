"""Open slot values -> numbers: "forty percent", "1 hour 30 minutes", "6:30 PM", "warm white"."""

from __future__ import annotations

import re

WORDS: dict[str, float] = {
    w: i
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]  # fmt: skip
    )
}
WORDS.update(
    {
        "twenty": 20,
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
        "hundred": 100,
        "a": 1,
        "an": 1,
        "half": 0.5,
    }
)
COLORS = {
    "white": "#ffffff",
    "warm white": "#ffd59e",
    "soft white": "#fff1d6",
    "cool white": "#e8f1ff",
    "daylight": "#f4f8ff",
    "red": "#e53935",
    "orange": "#fb8c00",
    "yellow": "#fdd835",
    "green": "#43a047",
    "blue": "#1e88e5",
    "purple": "#8e24aa",
    "pink": "#ec407a",
    "teal": "#00897b",
    "cyan": "#00bcd4",
    "magenta": "#d81b60",
    "violet": "#7e57c2",
    "indigo": "#3949ab",
    "lavender": "#b39ddb",
    "lilac": "#c8a2c8",
    "gold": "#ffc107",
    "amber": "#ffb300",
    "coral": "#ff7043",
    "salmon": "#fa8072",
    "peach": "#ffcc80",
    "mint": "#98ff98",
    "lime": "#cddc39",
    "turquoise": "#26c6da",
    "sky blue": "#81d4fa",
    "navy": "#1a237e",
    "royal blue": "#3f51b5",
    "maroon": "#880e4f",
    "brown": "#795548",
    "beige": "#d7ccc8",
    "ivory": "#fffff0",
    "gray": "#9e9e9e",
    "grey": "#9e9e9e",
    "silver": "#cfd8dc",
    "black": "#212121",
    "rose": "#f48fb1",
    "forest green": "#2e7d32",
    "hot pink": "#ff4081",
    "fuchsia": "#e040fb",
    "aqua": "#4dd0e1",
    "olive": "#827717",
}
UNKNOWN_COLOR = "#9aa0a6"


def number(text: str) -> float | None:
    """The first number in `text`: digits ("40", "2.5") or number words ("forty", "twenty five")."""
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match:
        return float(match.group())
    total, seen = 0.0, False
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in WORDS and word not in ("a", "an"):
            value = WORDS[word]
            total = total * value if value == 100 and seen else total + value
            seen = True
        elif seen and word != "and":
            break
    return total if seen else None


def percent(text: str) -> int | None:
    n = number(text)
    return None if n is None else round(n)


def degrees(text: str) -> int | None:
    return percent(text)


def duration_s(text: str) -> int | None:
    """Seconds in "45 minutes", "1 hour 30 minutes", "an hour", "half an hour", "5:00"."""
    low = text.lower().strip()
    clock = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", low)
    if clock:
        h, m, s = int(clock.group(1) or 0), int(clock.group(2)), int(clock.group(3))
        return (h * 3600 + m * 60 + s) if clock.group(1) else (m * 60 + s)
    if re.search(r"half an? hour", low):
        return 1800
    total, found = 0.0, False
    for amount, unit in re.findall(
        r"(\d+(?:\.\d+)?|[a-z]+(?:[ -][a-z]+)?)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b",
        low,
    ):
        n = number(amount) if not amount[0].isdigit() else float(amount)
        if n is None:
            n = 1 if amount.strip() in ("a", "an") else None
        if n is None:
            continue
        found = True
        total += n * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    return round(total) if found else None


def clock(text: str) -> tuple[int, int] | None:
    """(hour24, minute) of "6:30 AM", "7pm", "18:45", "6.30 p.m.", "noon", "midnight"."""
    low = text.lower().replace(".", ":" if re.search(r"\d\.\d{2}", text) else "").strip()
    if "noon" in low:
        return 12, 0
    if "midnight" in low:
        return 0, 0
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(a|p)?\s*m?\b", low.replace(":m", "m"))
    if not match:
        return None
    hour, minute, mer = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if mer and not 1 <= hour <= 12:
        return None
    if mer == "p" and hour < 12:
        hour += 12
    if mer == "a" and hour == 12:
        hour = 0
    return (hour, minute) if hour < 24 and minute < 60 else None


def clock_text(hour: int, minute: int) -> str:
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def css_color(name: str) -> str:
    """A CSS colour for any colour word or #hex; unknown words show as grey."""
    low = " ".join(name.lower().split())
    if re.fullmatch(r"#(?:[0-9a-f]{3}|[0-9a-f]{6})", low):
        return low
    if low in COLORS:
        return COLORS[low]
    words = low.split()
    for size in range(
        len(words) - 1, 0, -1
    ):  # "warm purple": its longest known tail, then any word
        if " ".join(words[-size:]) in COLORS:
            return COLORS[" ".join(words[-size:])]
    return next((COLORS[w] for w in reversed(words) if w in COLORS), UNKNOWN_COLOR)
