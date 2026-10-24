"""Fast path: common home commands answered without the LLM (R3, .scratch/latency/PLAN.md).

A turn takes the fast path only if the whole transcript matches one of a few exact phrasings
(after lower-casing, dropping punctuation, a leading "please"/"hey"/"okay" and a trailing
"please"/"thanks"; a leading "stop"/"wait" is already removed by doc_chat, and stripping it here
would turn "stop the music" into "the music"). The real tool runs, then a fixed confirmation is
spoken; on a tool error its own message is. Anything else goes to the LLM as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

UNITS = {"second": "seconds", "minute": "minutes", "hour": "hours"}
ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}  # fmt: skip
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}  # fmt: skip
NUMBER = r"(\d+|(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?: \w+)?)|\w+teen|\w+)"
LEADING = re.compile(r"^(?:(?:please|hey|okay|ok)\s+)+")
TRAILING = re.compile(r"(?:\s+(?:please|thanks|thank you))+$")


@dataclass(frozen=True)
class Match:
    tool: str
    args: dict[str, Any]
    say: str | None  # None: speak what the tool returns


def normalize(text: str) -> str:
    words = re.sub(r"[^a-z0-9: ]", " ", text.lower()).split()
    return TRAILING.sub("", LEADING.sub("", " ".join(words))).strip()


def number(words: str) -> int | None:
    """'45', 'forty five', 'ten' -> int; anything else None (0-100)."""
    if words.isdigit():
        return int(words)
    if words == "a hundred" or words == "one hundred":
        return 100
    parts = words.split()
    if len(parts) == 1:
        return ONES.get(parts[0], TENS.get(parts[0]))
    if len(parts) == 2 and parts[0] in TENS and parts[1] in ONES and ONES[parts[1]] < 10:
        return TENS[parts[0]] + ONES[parts[1]]
    return None


def clock(words: str) -> str | None:
    """'6:30 am', '7 pm', 'seven pm', 'six thirty am' -> '6:30 AM' / '7:00 PM' (the form the
    dry-run expectations and the routing prompt use); else None."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))? ?(am|pm)", words)
    if m:
        hour, minute = int(m.group(1)), m.group(2)
    else:
        m = re.fullmatch(r"(\w+)(?: (\w+(?: \w+)?))? (am|pm)", words)
        if not m:
            return None
        hour_n = number(m.group(1))
        minute_n = number(m.group(2)) if m.group(2) else None
        if hour_n is None or (m.group(2) and minute_n is None):
            return None
        hour, minute = hour_n, f"{minute_n:02d}" if minute_n is not None else None
    if not 1 <= hour <= 12 or (minute is not None and not 0 <= int(minute) <= 59):
        return None
    return f"{hour}:{minute or '00'} {m.group(3).upper()}"


FIXED: dict[str, tuple[str, str]] = {
    **dict.fromkeys(
        ["turn on the lights", "turn the lights on", "lights on", "switch on the lights"],
        ("LIGHT_ON", "The lights are on."),
    ),
    **dict.fromkeys(
        [
            "turn off the lights",
            "turn the lights off",
            "lights off",
            "switch off the lights",
            "kill the lights",
        ],
        ("LIGHT_OFF", "The lights are off."),
    ),  # fmt: skip
    **dict.fromkeys(
        ["play music", "play some music", "play the music"], ("PLAY_MUSIC", "Playing music.")
    ),  # fmt: skip
    **dict.fromkeys(["pause", "pause the music", "pause music"], ("PAUSE", "Paused.")),
    **dict.fromkeys(["stop the music", "stop music"], ("STOP", "Music stopped.")),
    **dict.fromkeys(
        ["next", "next song", "skip", "skip this song", "next track"],
        ("NEXT", "Skipping to the next one."),
    ),  # fmt: skip
    **dict.fromkeys(
        ["volume up", "turn it up", "turn the volume up", "louder"], ("VOLUME_UP", "Turning it up.")
    ),  # fmt: skip
    **dict.fromkeys(
        ["volume down", "turn it down", "turn the volume down", "quieter", "lower the volume"],
        ("VOLUME_DOWN", "Turning it down."),
    ),
}
TIMER = [
    re.compile(rf"set a timer for {NUMBER} (second|minute|hour)s?"),
    re.compile(rf"timer for {NUMBER} (second|minute|hour)s?"),
    re.compile(rf"{NUMBER} (second|minute|hour)s? timer"),
]
ALARM = [
    re.compile(r"set an alarm for (.+)"),
    re.compile(r"wake me up at (.+)"),
    re.compile(r"alarm for (.+)"),
]
SPEAKER = [
    re.compile(rf"set the speaker volume to {NUMBER} percent"),
    re.compile(rf"set the volume to {NUMBER} percent"),
    re.compile(rf"speaker volume {NUMBER}"),
]


def match(text: str) -> Match | None:
    """The fast-path action for an utterance, or None (then the LLM handles it)."""
    said = normalize(text)
    if said in FIXED:
        intent, confirm = FIXED[said]
        return Match("smart_home", {"intent": intent}, confirm)
    for pattern in TIMER:
        m = pattern.fullmatch(said)
        if m and (n := number(m.group(1))) is not None and n > 0:
            unit = UNITS[m.group(2)] if n != 1 else m.group(2)
            return Match(
                "smart_home",
                {"intent": "TIMER", "slot": f"{n} {unit}"},
                f"Timer set for {n} {unit}.",
            )
    for pattern in ALARM:
        m = pattern.fullmatch(said)
        if m and (when := clock(m.group(1))) is not None:
            return Match("smart_home", {"intent": "ALARM", "slot": when}, f"Alarm set for {when}.")
    for pattern in SPEAKER:
        m = pattern.fullmatch(said)
        if m and (n := number(m.group(1))) is not None and 0 <= n <= 100:
            return Match("set_volume", {"percent": n}, None)
    return None


def spoken_result(found: Match, result: Any) -> str:
    """What to say after the tool ran: the fixed confirmation, or the tool's own words."""
    if isinstance(result, dict):
        if result.get("ok") is False:
            return f"Sorry, that didn't work: {result.get('message', 'unknown error')}."
        return found.say or str(result.get("message", "Done."))
    return found.say or str(result)
