"""What to do with the local router's calls: run them here, or let Gemini take the turn.

Every decision about the router's output lives in `decide`: whether each call is something this
server can run and speak, the rules that keep a risky turn away from the small model (a question
while documents are loaded, unparseable durations, advice questions), and the final tool and
arguments of each call that runs (the trained `wiki_summary` becomes a `web_answer` lookup).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from voice_pipeline import fast_path
from voice_pipeline.question import is_question, needs_reasoning
from voice_pipeline.router import spec

CHAT_MAX_WORDS = 5  # a `none|` reply is a pleasantry ("thanks" -> "You're welcome."); a 1B model
# answers anything longer with an invention, so longer chat turns go to Gemini
# likewise a long reply: an invented joke is 13 words and a made-up capability speech 18; 10 lets
# through the trained "That sounds chilly. Want me to warm the room?" (9)
CHAT_REPLY_MAX_WORDS = 10
ECHO_MIN_WORDS = (
    3  # "Hello." -> "Hello!" is a pleasantry; a longer verbatim repeat is a garbled turn
)
INFO_TOOLS = {"weather", "wiki_summary", "web_answer", "get_volume", "set_volume"}
DOCUMENT_LOOKUPS = {"web_answer", "wiki_summary"}  # the lookups that race the documents


@dataclass
class Planned:
    """One call as it will run: `tool` and `args` are final (the swap is applied)."""

    call: Any
    tool: str
    args: dict[str, Any]
    home: bool = False


@dataclass
class Decision:
    reason: str | None  # None: run `planned` here; else why Gemini takes the turn
    planned: list[Planned] = field(default_factory=list)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def _echoes(user: str, reply: str) -> bool:
    """The reply just repeats what the user said: the recogniser garbled the turn ("Bucket on Mu.")
    and the model parroted it, which is no answer."""
    said, back = _words(user), _words(reply)
    if len(said.split()) >= ECHO_MIN_WORDS and said in back:
        return True
    return len(back.split()) >= ECHO_MIN_WORDS and back in said


_ONE_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
)
_ONES = _ONE_WORDS.split()
_TEN_WORDS = "twenty thirty forty fifty sixty seventy eighty ninety"
_TENS = _TEN_WORDS.split()
_NUMBER_WORDS = set(_ONES) | set(_TENS) | {"hundred", "couple"}
_UNITS = {
    "second", "seconds", "sec", "secs", "s", "minute", "minutes", "min", "mins", "m",
    "hour", "hours", "hr", "hrs", "h",
}  # fmt: skip
_QUANTITY = re.compile(r"\d+(?:\.\d+)?")
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[a-z]+")


def _number_at(tokens: list[str], i: int) -> int:
    """How many tokens from `i` make a quantity ("a", "half an", "forty five", "1.5"); 0 if none."""
    j = i
    while j < len(tokens) and (
        _QUANTITY.fullmatch(tokens[j])
        or tokens[j] in _NUMBER_WORDS
        or tokens[j] in ("a", "an", "half")
    ):
        j += 1
    return j - i


def is_duration(text: str) -> bool:
    """ "20 minutes", "an hour", "half an hour", "1 hour 30 minutes", "an hour and a half"."""
    tokens = _TOKEN.findall(text.lower().replace("-", " "))
    i, groups = 0, 0
    while i < len(tokens):
        if tokens[i] == "and":
            i += 1
            if tokens[i:] in (["a", "half"], ["half"]) and groups:
                return True
            continue
        n = _number_at(tokens, i)
        if n == 0 or i + n >= len(tokens) or tokens[i + n] not in _UNITS:
            return False
        i += n + 1
        groups += 1
    return groups > 0


_DAY_WORDS = {
    "at", "on", "in", "the", "of", "next", "this", "tomorrow", "today", "tonight", "morning",
    "afternoon", "evening", "night", "oclock", "clock", "o", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday",
}  # fmt: skip
_CLOCK = re.compile(r"(\d{1,2})(?::?(\d{2}))?")


def _spoken_number(tokens: list[str]) -> int | None:
    """ "seven" -> 7, "forty five" -> 45; None if the tokens are not one such number."""
    if len(tokens) == 1 and tokens[0] in _ONES:
        return _ONES.index(tokens[0])
    if tokens and tokens[0] in _TENS:
        base = (_TENS.index(tokens[0]) + 2) * 10
        if len(tokens) == 1:
            return base
        if len(tokens) == 2 and tokens[1] in _ONES[1:10]:
            return base + _ONES.index(tokens[1])
    return None


def is_time_of_day(text: str) -> bool:
    """ "6:30 AM", "7 pm", "seven thirty", "noon", "half past six", "tomorrow at 6 am"."""
    flat = text.lower().replace(".", "").replace("-", " ").replace("'", "")
    tokens = [t for t in _TOKEN.findall(flat) if t not in _DAY_WORDS]
    suffix = tokens.pop() if tokens and tokens[-1] in ("am", "pm") else None
    if tokens in (["noon"], ["midnight"]):
        return suffix is None
    if len(tokens) >= 2 and tokens[0] in ("half", "quarter") and tokens[1] in ("past", "to"):
        tokens = tokens[2:]
        hour = _spoken_number(tokens) if tokens else None
        if hour is None:
            hour = int(tokens[0]) if len(tokens) == 1 and tokens[0].isdigit() else None
        return hour is not None and 0 < hour <= 12
    if not tokens:
        return False
    if len(tokens) == 1 and (m := _CLOCK.fullmatch(tokens[0])):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
    elif all(t.isdigit() for t in tokens) and len(tokens) == 2:
        hour, minute = int(tokens[0]), int(tokens[1])
    else:
        for cut in range(1, len(tokens) + 1):  # "six forty five": hour words, then minute words
            first, rest = _spoken_number(tokens[:cut]), tokens[cut:]
            if first is not None and (not rest or _spoken_number(rest) is not None):
                hour, minute = first, (_spoken_number(rest) if rest else 0) or 0
                break
        else:
            return False
    return (hour <= 12 if suffix else hour <= 24) and minute < 60


def _alarm_time(text: str) -> bool:
    return is_time_of_day(text) or is_duration(text)


TIME_CHECKS: dict[str, Callable[[str], bool]] = {"TIMER": is_duration, "ALARM": _alarm_time}


def _verdict(calls: list[Any], available: set[str], user: str) -> str | None:
    if not calls:
        return "empty"
    for call in calls:
        name = call.name
        if name == "escalate":
            return "escalate"
        if name == "none":
            if len(calls) > 1 or not call.args or not call.args[0].strip():
                return "unsupported: none"
            if len(user.split()) > CHAT_MAX_WORDS:
                return "chat too long"
            if len(call.args[0].split()) > CHAT_REPLY_MAX_WORDS:
                return "chat reply too long"
            if _echoes(user, call.args[0]):
                return "echo"
            if "search_docs" in available and is_question(user):
                return "documents loaded: question"  # a 1B model would invent the answer
        elif name in spec.HOME_INTENTS:
            if name == "LIST_REMINDERS" or "smart_home" not in available:
                return f"unsupported: {name}"
            check = TIME_CHECKS.get(name)
            if check is not None and not check(call.args[0] if call.args else ""):
                return f"unsupported: {name} value"
        elif name == "get_current_time":
            try:
                ZoneInfo(spec.to_tool_args(call).get("timezone", fast_path.TIMEZONE))
            except (ZoneInfoNotFoundError, ValueError):
                return "unsupported: time zone"
        elif name not in INFO_TOOLS or name not in available:
            return f"unsupported: {name}"
        elif needs_reasoning(user):  # "should I bring an umbrella": a decision, not a forecast
            return "needs reasoning"
    return None


def plan(calls: list[Any], user: str, available: set[str]) -> list[Planned]:
    """The tool and arguments each call runs with. The router was trained on `wiki_summary`; the
    web answer replaces it (Wikipedia is its fallback). A lookup that is the turn's only call asks
    the whole question, which finds more than the router's topic."""
    out: list[Planned] = []
    asked = user.strip()
    for call in calls:
        home = call.name in spec.HOME_INTENTS
        if home:
            args: dict[str, Any] = {"intent": call.name}
            if call.args:
                args["slot"] = call.args[0]
            out.append(Planned(call, "smart_home", args, True))
            continue
        tool = call.name
        args = spec.to_tool_args(call) if tool in spec.BY_NAME else {}
        if tool == "wiki_summary" and "web_answer" in available:
            tool, args = "web_answer", {"query": args["topic"]}
        if tool == "web_answer" and len(calls) == 1 and asked and "query" in args:
            args = {**args, "query": asked}
        out.append(Planned(call, tool, args))
    return out


def decide(calls: list[Any], user: str, available: set[str]) -> Decision:
    reason = _verdict(calls, available, user)
    return Decision(reason, [] if reason else plan(calls, user, available))
