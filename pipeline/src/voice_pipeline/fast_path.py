"""Fast path: common home commands answered without the LLM (R3, .scratch/latency/PLAN.md).

A turn takes the fast path only if the whole transcript matches one of a few exact phrasings
(after lower-casing, dropping punctuation, a leading "please"/"hey"/"okay" and a trailing
"please"/"thanks"; a leading "stop"/"wait" is already removed by doc_chat, and stripping it here
would turn "stop the music" into "the music"). The real tool runs, then a fixed confirmation is
spoken; on a tool error its own message is. Anything else goes to the LLM as before. The clock
questions in `LOCAL` ("what time is it", "what's the date") are answered from the clock, with no
tool either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from voice_pipeline import selfinfo, venue

TIMEZONE = "Asia/Manila"  # the demo's time zone (the time server is told the same)
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
# Questions the clock answers with no tool and no LLM: "time" or "date" (the demo's own time zone;
# other cities, "in Tokyo", are not listed here and go to the time tool).
LOCAL: dict[str, str] = {
    **dict.fromkeys(
        [
            "what time is it",
            "what time is it now",
            "what time is it right now",
            "what is the time",
            "what is the time now",
            "what s the time",
            "tell me the time",
            "do you have the time",
            "current time",
        ],
        "time",
    ),
    **dict.fromkeys(
        [
            "what is the date",
            "what is the date today",
            "what is today s date",
            "what s the date",
            "what s the date today",
            "what s today s date",
            "what day is it",
            "what day is it today",
            "what day is today",
        ],
        "date",
    ),
    **dict.fromkeys(
        [
            "where are we",
            "where are we now",
            "where are we right now",
            "where am i",
            "where am i now",
            "where am i right now",
            "where is this",
            "where are we located",
            "what city are we in",
            "what city is this",
            "what is our location",
            "what s our location",
            "what is my location",
            "what s my location",
            "tell me where we are",
            "tell me where we are right now",
            "can you tell me where we are",
            "can you tell me where we are right now",
            "can you tell me where we are now",
        ],
        "place",
    ),
    **dict.fromkeys(
        ["what is my name", "what s my name", "whats my name", "do you know my name",
         "tell me my name", "can you tell me my name", "do you remember my name"],
        "self:name",
    ),
    **dict.fromkeys(
        ["where do i work", "what do i do", "what do i do for work", "what is my job",
         "what s my job", "what is my occupation", "what s my occupation", "who is my employer",
         "what is my role", "what s my role"],
        "self:work",
    ),
    **dict.fromkeys(
        ["where do i study", "what do i study", "what am i studying", "what school do i go to",
         "where do i go to school", "what is my school", "what s my school", "what is my degree",
         "what s my degree", "what is my degree program"],
        "self:study",
    ),
    **dict.fromkeys(
        ["where do i live", "what city do i live in", "what is my city", "what s my city"],
        "self:city",
    ),
    **dict.fromkeys(
        ["what course am i taking", "what class am i taking", "what is my course",
         "what s my course", "who is my instructor", "who is my professor",
         "who s my professor", "who is my teacher", "what is my current course"],
        "self:course",
    ),
    **dict.fromkeys(
        ["tell me about myself", "tell me about me", "who am i", "what do you know about me",
         "what do you know about myself", "describe me"],
        "self:about",
    ),
    **dict.fromkeys(
        [
            "what barangay is this",
            "what barangay are we in",
            "what barangay are we in right now",
            "which barangay are we in",
            "which barangay are we in right now",
            "what barangay am i in",
            "what barangay am i in right now",
            "which barangay am i in",
            "which barangay am i in right now",
            "what is our barangay",
            "what s our barangay",
            "can you tell me which barangay we are in",
            "can you tell me what barangay we are in",
        ],
        "barangay",
    ),
}  # fmt: skip
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
    if said in LOCAL:
        what = LOCAL[said]
        if what.startswith("self:") and selfinfo.answer(what[5:]) is None:
            return None  # the profile does not say: the model answers as before
        return Match("clock", {"what": what}, None)
    for what, patterns in (("place", PLACE_PHRASES), ("barangay", BARANGAY_PHRASES)):
        if any(pattern.fullmatch(said) for pattern in patterns):
            return Match("clock", {"what": what}, None)
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


# "Can you then tell me where I am right now", "check the location of where I am", "what's my
# current location": the spoken forms of the fixed place questions. Whole-utterance matches only, so
# "where are we in the build" still goes to the model.
_ASK = r"(?:(?:can|could|would|will) you )?(?:(?:then|also|now|just|quickly|actually) )*"
_VERB = r"(?:(?:tell me|check|find out|figure out|look up|let me know|show me|say|give me) )?"
_NOW = r"(?: (?:right now|now|currently|at the moment|at present|today))?"
_HERE = r"(?:where (?:exactly )?(?:am i|we are|are we|i am)(?: located| at)?)"
_SITE = r"(?:the )?(?:current |exact |present )?(?:location|position)"
PLACE_PHRASES = tuple(
    re.compile(f"{_ASK}{_VERB}{body}{_NOW}")
    for body in (
        _HERE,
        rf"{_SITE} (?:of )?{_HERE}",
        r"(?:what s |what is |whats )?(?:my|our) (?:current |exact |present )?"
        r"(?:location|position)(?: is)?",
        r"(?:what|which) (?:place|city|area|part of (?:town|the city|cebu)) "
        r"(?:am i|are we) (?:in|at)",
        rf"(?:what s |what is |whats )?{_SITE} (?:of )?(?:me|us)",
    )
)
BARANGAY_PHRASES = tuple(
    re.compile(f"{_ASK}{_VERB}{body}{_NOW}")
    for body in (
        r"(?:what|which) barangay (?:am i|are we|is this|i am|we are)(?: in| at)?",
        r"(?:what s |what is |whats )?(?:my|our|the current) barangay",
        r"(?:the )?barangay (?:of )?where (?:i am|we are)(?: located)?",
    )
)


DATE_ASKED = re.compile(
    r"\bdate\b|\b(?:what|which) (?:day|month|year)\b|\bday of the (?:week|month)\b"
)
TIME_ASKED = re.compile(r"\btime\b|\bo clock\b")


def clock_question(text: str) -> str:
    """What a question to the clock wants: "time" (also the default), "date" or "both"."""
    said = normalize(text)
    date, time = DATE_ASKED.search(said) is not None, TIME_ASKED.search(said) is not None
    return "both" if date and time else "date" if date else "time"


def local_answer(found: Match, now: datetime) -> str:
    """The reply to a `clock` match, given the current time in the demo's time zone."""
    what = found.args["what"]
    if what.startswith("self:"):
        return selfinfo.answer(what[5:]) or ""
    if what in ("place", "barangay"):
        return venue.spoken(asked_barangay=what == "barangay")
    clock_text = f"It's {now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}."
    date_text = f"Today is {now:%A}, {now:%B} {now.day}."
    if what == "time":
        return clock_text
    return date_text if what == "date" else f"{clock_text} {date_text}"


def spoken_result(found: Match, result: Any) -> str:
    """What to say after the tool ran: the fixed confirmation, or the tool's own words."""
    if isinstance(result, str) and result.strip():  # the music tool's sentence ("Playing X.")
        return result.strip()
    if isinstance(result, dict):
        if result.get("ok") is False:
            return f"Sorry, that didn't work: {result.get('message', 'unknown error')}."
        return found.say or str(result.get("message", "Done."))
    return found.say or str(result)
