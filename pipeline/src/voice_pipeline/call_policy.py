"""What to do with the local router's calls: run them here, or let Gemini take the turn.

Every decision about the router's output lives in `decide`: whether each call is something this
server can run and speak, the rules that keep a risky turn away from the small model (a question
while documents are loaded, unparseable durations, advice questions), and the final tool and
arguments of each call that runs (the trained `wiki_summary` becomes a `web_answer` lookup).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from voice_pipeline import device_say, fast_path, place_check
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
# The old router's device_status calls were mostly wrong (a ping or a volume change answered with
# the Pi's status); kd-web3 scored Pi 12/12 held out, and `device_question` still guards each call.
ROUTER_DEVICE_STATUS = True
DOCUMENT_LOOKUPS = {"web_answer", "wiki_summary"}  # the lookups that race the documents


# A short "yes" to the assistant's own offer ("Would you like me to open it?") means what the offer
# said: the 350M router has no context for it and answered "Okay." three times in the 2026-11-05
# session, doing nothing. Gemini sees the whole conversation.
OFFER = re.compile(
    r"\?\s*$|want me to|would you like|do you want|shall i|should i|say open it|say yes",
    re.IGNORECASE,
)
CONFIRM_WORDS = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "no", "nope", "nah"}
    | {"do", "it", "go", "ahead", "that", "one", "in", "the", "browser", "open", "right", "now"}
)
CONFIRM_ANCHORS = frozenset({"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "no"})
CONFIRM_MAX_WORDS = 6


def answers_an_offer(messages: list[dict[str, Any]], router_replies: Any = ()) -> bool:
    """The last user turn is a short yes/no made of confirmation words, and the assistant's reply
    before it ended with a question or an offer that the router did not write: the router's own
    follow-ups ("Want me to warm the room?" then "To how many degrees?") are trained and stay."""
    if len(messages) < 2 or messages[-1].get("role") != "user":
        return False
    previous = messages[-2]
    reply = str(previous.get("content", "")).strip()
    if previous.get("role") != "assistant" or reply in router_replies or not OFFER.search(reply):
        return False
    words = re.findall(r"[a-z']+", str(messages[-1].get("content", "")).lower())
    return (
        0 < len(words) <= CONFIRM_MAX_WORDS
        and all(w in CONFIRM_WORDS for w in words)
        and any(w in CONFIRM_ANCHORS or w in ("nope", "nah") for w in words)
    )


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


# a pleasantry is answered with itself ("Good morning." -> "Good morning!")
SOCIAL = frozenset(
    {
        "hello",
        "hi",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "good night",
        "goodbye",
        "bye",
        "thanks",
        "thank you",
    }
)


def _echoes(user: str, reply: str) -> bool:
    """The reply just repeats what the user said: the recogniser garbled the turn ("Bucket on Mu.",
    a stop word heard as "Wind storm.") and the model parroted it, which is no answer."""
    said, back = _words(user), _words(reply)
    if said and said == back and said not in SOCIAL:
        return True
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


_PI_WORDS = re.compile(r"\b(pi|pie|raspberry|speaker|device)\b")
_NOT_STATUS = re.compile(
    r"\b(volume|percent|louder|quieter|ping|latency|network|internet|set|change|make|turn|put)\b"
    r"|\bper ?cent\b|\b\d+\b|\b(zero|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|hundred|half|full|max|mute)\b"  # a level: a volume change the transcript garbled ("sata")
)


def device_question(user: str) -> bool:
    """The user asks the Pi or speaker for a status topic (not a volume, ping or other command)."""
    asked = _words(user)
    return (
        _PI_WORDS.search(asked) is not None
        and any(pattern.search(asked) for pattern in device_say.TOPICS.values())
        and _NOT_STATUS.search(asked) is None
    )


_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "do",
        "does",
        "did",
        "what",
        "who",
        "whom",
        "whose",
        "which",
        "when",
        "where",
        "why",
        "how",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "i",
        "me",
        "my",
        "you",
        "your",
        "we",
        "our",
        "they",
        "their",
        "he",
        "she",
        "his",
        "her",
        "him",
        "with",
        "about",
        "from",
        "by",
        "as",
        "so",
        "not",
        "no",
        "yes",
        "please",
        "can",
        "could",
        "would",
        "should",
        "will",
        "tell",
        "say",
        "give",
        "get",
        "let",
        "know",
        "any",
        "some",
    ]
)


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def _lookup_asked(user: str, argument: str) -> bool:
    return is_question(user) or bool(_content(user) & _content(argument))


def _numbers(text: str) -> set[int]:
    """Every integer in the text, digits or number words ("six thirty" -> 6, 30; "twenty eight"
    -> 28, 20, 8; "6:30am" -> 6, 30)."""
    tokens = re.findall(r"\d+|[a-z]+", text.lower().replace("-", " "))
    found: set[int] = set()
    for i, token in enumerate(tokens):
        if token.isdigit():
            found.add(int(token))
        elif token in _ONES:
            found.add(_ONES.index(token))
        elif token in _TENS:
            tens = (_TENS.index(token) + 2) * 10
            found.add(tens)
            if i + 1 < len(tokens) and tokens[i + 1] in _ONES[1:10]:
                found.add(tens + _ONES.index(tokens[i + 1]))
    return found


_FRACTION_UNIT = re.compile(
    r"\b(a|an|half|quarter)\s+(?:(?:a|an)\s+)?(second|minute|hour|sec|min|hr)s?\b"
)
NUMERIC_SLOTS = {"TIMER", "ALARM", "BRIGHTNESS", "TEMPERATURE"}


def slot_from_user(slot: str, user: str, durational: bool) -> bool:
    """Every number of the slot was said in this turn (not carried over from the history); a
    duration with no number needs "a / an / half / quarter" plus a unit in the user's words."""
    wanted = _numbers(slot)
    if wanted:
        return wanted <= _numbers(user)
    if durational and is_duration(slot):
        return _FRACTION_UNIT.search(user.lower()) is not None
    return True


# "play" is overloaded: the 350M router tagged 15 of 24 game phrasings ("Can I play the Angry Birds
# clone?") PLAY_MUSIC, and the home tool then played music (2026-11-06). A play/open/launch/run turn
# stays with the router only when it is plain music; anything that names a target goes to Gemini,
# which sees the apps and can pass an artist.
PLAY_VERBS = frozenset({"play", "open", "launch", "run", "start", "resume"})
NON_MUSIC_VERBS = frozenset({"play", "open", "launch", "run"})  # "start" is also a timer verb
MUSIC_WORDS = frozenset(
    {"music", "song", "songs", "playlist", "radio", "something", "tunes", "next", "previous"}
    | {"another", "different", "other"}  # "play the next song", "play another one"
)
VERB_FILLER = frozenset(
    {"please", "can", "could", "would", "will", "you", "the", "a", "an", "some", "me", "i", "my"}
    | {"to", "for", "now", "again", "just", "go", "ahead", "let", "lets", "let's", "like", "want"}
    | {"id", "i'd", "us", "we", "on", "up", "back", "hey", "okay", "ok", "again"}
)
APPS_CACHE_S = 5.0
_apps_cache: tuple[float, list[str]] = (0.0, [])


def known_apps() -> list[str]:
    """The names of the web apps that exist (agent_tools.app_names), read at most every few
    seconds; tests replace this function."""
    global _apps_cache
    now = time.monotonic()
    if now - _apps_cache[0] > APPS_CACHE_S or not _apps_cache[0]:
        from voice_pipeline.agent_tools import app_names

        _apps_cache = (now, app_names())
    return _apps_cache[1]


def names_an_app(user: str) -> bool:
    """The user's words contain the name of an existing app (angry birds, flappy bird)."""
    from voice_pipeline.agent_tools import app_key

    spoken = set(app_key(user).split())
    return any(app_key(app) and set(app_key(app).split()) <= spoken for app in known_apps())


MUSIC_VERBS = frozenset({"put", "turn", "listen", "hear", "wanna", "gonna", "need"})


def _rest(user: str) -> set[str]:
    """The words that are neither a play verb nor filler: what the user named."""
    words = re.findall(r"[a-z']+", user.lower())
    return {w for w in words if w not in PLAY_VERBS and w not in VERB_FILLER}


def _clauses(user: str) -> list[str]:
    return re.split(r",|;|\band\b|\bthen\b|\balso\b", user.lower())


def bare_music(user: str) -> bool:
    """Only a verb, filler and music words: "play some music", "start the music", "play",
    "can you play something", "resume the music"."""
    return bool(set(re.findall(r"[a-z']+", user.lower())) & PLAY_VERBS) and _rest(user) <= (
        MUSIC_WORDS
    )


def play_target(call_name: str, user: str, single: bool = True) -> str | None:
    """Why a router home intent for a play/open/launch/run turn must go to Gemini, or None.
    PLAY_MUSIC is judged on the clause(s) that talk about playing ("turn off the lights and play
    some music" is fine); other intents only when they are the turn's only call."""
    if call_name == "PLAY_MUSIC":  # also without a verb ("some music please", "put on jazz")
        for clause in _clauses(user):
            words = set(re.findall(r"[a-z']+", clause))
            plays = words & (PLAY_VERBS | MUSIC_WORDS | {"listen", "hear"}) or "put on" in clause
            if plays and not _rest(clause) - MUSIC_VERBS <= MUSIC_WORDS:
                return "play: names a target"
        return None
    words = set(re.findall(r"[a-z']+", user.lower()))
    if not single:
        return None
    if "resume" in words and call_name != "PLAY_MUSIC":
        return "resume is not " + call_name
    if words & NON_MUSIC_VERBS and not bare_music(user):
        return f"{call_name} for a play/open turn"
    return None


# W1 negation: "don't turn off the lights" made the router turn them off (2026-11-06 probe).
_NEGATION = re.compile(
    r"\b(don't|dont|do not|does not|never(?! mind)|no need to)\b|(?<![a-z])'?wag\b|\bhuwag\b",
    re.IGNORECASE,
)
_STOP_ING = re.compile(r"\bstop \w+ing\b", re.IGNORECASE)


def negated_action(call_name: str, user: str) -> str | None:
    """A home action on a turn that says not to: the router does not read negation."""
    if call_name not in spec.HOME_INTENTS:
        return None
    if _NEGATION.search(user):
        return f"negation: {call_name}"
    if _STOP_ING.search(user) and call_name not in ("STOP", "PAUSE"):
        return f"negation: {call_name}"
    return None


SORRY_MAX_WORDS = 3  # W2: "Sorry, I didn't catch that." fits a garbled blip, not a sentence


def _is_sorry(reply: str) -> bool:
    return reply.lower().startswith("sorry") and "catch" in reply.lower()


def _set(text: str) -> frozenset[str]:
    return frozenset(text.split())


# W3 keyword gates: the router must have had a reason in the user's words
GATES: dict[str, frozenset[str]] = {
    "weather": _set(
        "weather rain raining rainy forecast sunny cloud clouds cloudy umbrella storm typhoon hot "
        "cold warm cool humid wind windy snow temperature outside panahon ulan init lamig "
        "bagyo today tomorrow tonight morrow weekend"  # "And tomorrow?" after a forecast
    ),
    "get_current_time": _set(
        "time date day month year clock oclock o'clock hour late week today oras petsa araw"
    ),
    "LIGHT_OFF": _set("off out patay patayin kill shut dark sleep bed night"),
    "TEMPERATURE": _set(
        "degrees degree temperature thermostat aircon heat warm cool cold hot celsius init lamig"
    ),
    "NEXT": _set("next skip susunod another forward following different"),
}
_TIME_QUERY = re.compile(r"\btime\b", re.IGNORECASE)


_FOLLOW_UP = re.compile(r"\s*(and|what about|how about|then|also)\b", re.IGNORECASE)


def gate_missing(call: Any, user: str) -> str | None:
    words = set(re.findall(r"[a-z']+", user.lower()))
    gate = GATES.get(call.name)
    follow_up = call.name in ("weather", "get_current_time") and _FOLLOW_UP.match(user)
    if gate is not None and not (words & gate) and not follow_up:
        return f"no keyword for {call.name}"
    if call.name == "LIGHT_OFF" and words & {"dim", "dimmer", "dimmed"}:
        return "dim is not off"
    if (
        call.name in ("web_answer", "wiki_summary")
        and call.args
        and _TIME_QUERY.search(call.args[0])
        and not words & GATES["get_current_time"]
    ):
        return "a time query the user did not ask"
    return None


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
            if names_an_app(user):
                return "names an app"
            if _is_sorry(call.args[0]) and len(user.split()) > SORRY_MAX_WORDS:
                return "sorry on a sentence"  # "Open angry birds." -> "Sorry, I didn't catch that."
        elif name in spec.HOME_INTENTS:
            if name == "LIST_REMINDERS" or "smart_home" not in available:
                return f"unsupported: {name}"
            if (why := play_target(name, user, len(calls) == 1)) is not None:
                return why
            check = TIME_CHECKS.get(name)
            if check is not None and not check(call.args[0] if call.args else ""):
                return f"unsupported: {name} value"
            if name in NUMERIC_SLOTS and not slot_from_user(
                call.args[0] if call.args else "", user, name in TIME_CHECKS
            ):
                return f"slot not in the user's words: {name}"
            if (why := negated_action(name, user)) is not None:
                return why
            if (why := gate_missing(call, user)) is not None:
                return why
        elif name == "get_current_time":
            try:
                ZoneInfo(spec.to_tool_args(call).get("timezone", fast_path.TIMEZONE))
            except (ZoneInfoNotFoundError, ValueError):
                return "unsupported: time zone"
            if (why := gate_missing(call, user)) is not None:
                return why
        elif name == "device_status" and ROUTER_DEVICE_STATUS and "device_status" in available:
            if not device_question(user):
                return "unsupported: device_status"
        elif name not in INFO_TOOLS or name not in available:
            return f"unsupported: {name}"
        elif needs_reasoning(user):  # "should I bring an umbrella": a decision, not a forecast
            return "needs reasoning"
        elif name in ("weather", "web_answer", "wiki_summary") and (
            why := gate_missing(call, user)
        ):
            return why
        elif name == "weather" and not place_check.known_place(
            spec.to_tool_args(call).get("place", "")
        ):
            return "unknown place"  # W4: Gemini corrects or asks
        elif name in DOCUMENT_LOOKUPS and not _lookup_asked(
            user, call.args[0] if call.args else ""
        ):
            return "not a question"  # a statement is not looked up
        elif name == "set_volume" and not slot_from_user(
            call.args[0] if call.args else "", user, False
        ):
            return "slot not in the user's words: set_volume"
    return None


def plan(calls: list[Any], user: str, available: set[str]) -> list[Planned]:
    """The tool and arguments each call runs with. The router was trained on `wiki_summary`; the
    web answer replaces it (Wikipedia is its fallback). A swapped lookup that is the turn's only
    call asks the whole question, which finds more than the router's topic; a `web_answer` keeps
    the router's own question (it resolved the follow-up's context from the history)."""
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
            topic = args.get("topic", "").strip()
            tool, args = "web_answer", {"query": asked if len(calls) == 1 and asked else topic}
        if tool == "web_answer":
            args = {"query": str(args.get("query", "")).strip() or asked}
        out.append(Planned(call, tool, args))
    return out


def decide(calls: list[Any], user: str, available: set[str]) -> Decision:
    reason = _verdict(calls, available, user)
    return Decision(reason, [] if reason else plan(calls, user, available))
