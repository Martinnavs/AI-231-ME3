"""Generate the router's training data: exact target lines from seeds, consumer-style wording.

    python3 data/generate.py --out data/generated --n 25000 --seed 0 [--teacher gemini --teacher-share 0.5]

Writes train.jsonl, dev.jsonl, test.jsonl and stats.json in --out. One example per line:
    {"id", "split", "kind", "messages": [system, ..., user, assistant], "target"}
The assistant message is the target, written by `spec.format_call`, so every label parses by construction.
Plain templates give wording without any network; `--teacher` asks a hosted model to rewrite a share of the
utterances in more natural, noisy consumer speech (the key is read at run time and never printed). The two
evaluation sets (the 87-prompt pack and eval/onit_eval.jsonl) are never trained on: near-duplicates are dropped.
Splits are made by seed key, so a template/slot pairing never appears in two splits.

Mix (plan section 2): single turn about 60%, multi-turn with history 20%, two independent calls 6%, deferrals
15% (tools/code/workflow/reasoning/other), plus pleasantries and missing-value questions.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "spec"))
import banks as B  # noqa: E402
import tools as T  # noqa: E402
from format import build_messages  # noqa: E402

ROOT = HERE.parents[1]
Example = dict  # {"kind", "key", "phonebook", "history", "user", "target"}


# ------------------------------------------------------------------------------------------ spoken forms
ONES = [
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
]
TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}


def words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10 * 10] + (f" {ONES[n % 10]}" if n % 10 else "")
    return "one hundred" if n == 100 else str(n)


def spoken_number(n: int, rng: random.Random) -> str:
    return words(n) if rng.random() < 0.4 else str(n)


def time_forms(hour: int, minute: int, ampm: str, rng: random.Random) -> str:
    """A spoken form of a clock time; the target is always `H:MM AM`."""
    h12 = hour
    if rng.random() < 0.35:
        return f"{h12}:{minute:02d} {ampm}"
    part = "in the morning" if ampm == "AM" else ("in the evening" if hour != 12 else "noon")
    if minute == 0:
        return rng.choice([f"{words(h12)} o'clock {part}", f"{words(h12)} {ampm.lower()[0]} m", f"{h12} {ampm}"])
    if minute == 30:
        return rng.choice(
            [f"half past {words(h12)} {part}", f"{words(h12)} thirty {ampm.lower()[0]} m", f"{h12}:30 {ampm}"]
        )
    return f"{words(h12)} {words(minute)} {part}"


def spoken_slot(name: str, slot: str, rng: random.Random) -> str:
    """The user's wording for a slot value (the target keeps the canonical form)."""
    if name == "BRIGHTNESS":
        n = int(slot.split()[0])
        return rng.choice([f"{spoken_number(n, rng)} percent", f"{n}%"])
    if name == "TEMPERATURE":
        n = int(slot.split()[0])
        return rng.choice([f"{spoken_number(n, rng)} degrees", f"{n}"])
    if name == "TIMER":
        out = []
        for chunk in re.findall(r"(\d+) (\w+)", slot):
            n, unit = int(chunk[0]), chunk[1]
            out.append(f"{spoken_number(n, rng)} {unit}")
        return " ".join(out)
    return slot


# ------------------------------------------------------------------------------------------ scenarios
_FORCED_BOOK: dict[str, str] | None = None


def phonebook(rng: random.Random) -> dict[str, str]:
    if _FORCED_BOOK is not None:  # a history and its final turn share one phonebook
        return dict(_FORCED_BOOK)
    return dict(rng.sample(B.CONTACT_NAMES, rng.randint(4, 7)))


@contextlib.contextmanager
def shared_book(rng: random.Random):
    """Every maker called inside uses this one phonebook, so earlier turns and the final turn agree."""
    global _FORCED_BOOK
    book = phonebook(rng)
    _FORCED_BOOK = book
    try:
        yield book
    finally:
        _FORCED_BOOK = None


def line(name: str, *args: str) -> str:
    return T.format_call(T.Call(name, list(args)))


def ex(kind: str, key: str, user: str, target: list[str], book: dict[str, str], history=None) -> Example:
    return {
        "kind": kind,
        "key": key,
        "phonebook": book,
        "history": history or [],
        "user": user,
        "target": "\n".join(target),
    }


def s_noslot(rng: random.Random) -> Example:
    name = rng.choice(list(B.NO_SLOT))
    text = rng.choice(B.NO_SLOT[name])
    return ex("home", f"{name}:{text}", text, [line(name)], phonebook(rng))


def s_slot(rng: random.Random) -> Example:
    name = rng.choice(list(B.SLOT_TEMPLATES))
    template = rng.choice(B.SLOT_TEMPLATES[name])
    if name == "BRIGHTNESS":
        slot = f"{rng.randrange(10, 101, 5)} percent"
    elif name == "COLOR":
        slot = rng.choice(B.COLORS)
    elif name == "TEMPERATURE":
        slot = f"{rng.randint(16, 30)} degrees"
    elif name == "TIMER":
        slot = rng.choice(B.DURATIONS)
    elif name == "ALARM":
        hour, minute, ampm = rng.randint(1, 12), rng.choice([0, 15, 30, 45]), rng.choice(["AM", "PM"])
        slot = f"{hour}:{minute:02d} {ampm}"
        text = template.format(slot=time_forms(hour, minute, ampm, rng))
        return ex("home", f"{name}:{template}:{slot}", text, [line(name, slot)], phonebook(rng))
    else:  # CREATE_REMINDER
        slot = rng.choice(B.TASKS)
    text = template.format(slot=spoken_slot(name, slot, rng))
    return ex("home", f"{name}:{template}:{slot}", text, [line(name, slot)], phonebook(rng))


def s_contact(rng: random.Random) -> Example:
    book = phonebook(rng)
    name = rng.choice(list(book))
    alias = rng.choice([name, f"my {book[name]}"])
    verb = rng.choice(["CALL", "MESSAGE"])
    if verb == "CALL":
        text = rng.choice(["call {a}", "phone {a}", "ring {a}", "give {a} a call", "can you call {a}"]).format(a=alias)
    else:
        text = rng.choice(
            [
                "text {a}",
                "send a message to {a}",
                "message {a}",
                "tell {a} i'm on my way",
                "text {a} that i'm running late",
            ]
        ).format(a=alias)
    return ex("contact", f"{verb}:{alias}:{text}", text, [line(verb, name)], book)


def s_contact_missing(rng: random.Random) -> Example:
    book = phonebook(rng)
    who = rng.choice(B.NOT_IN_BOOK)
    verb = rng.choice(["call", "text", "message"])
    text = f"{verb} {who}"
    return ex("contact-missing", f"missing:{text}", text, [line("none", f"{who} is not in the phonebook.")], book)


def s_time(rng: random.Random) -> Example:
    if rng.random() < 0.4:
        text = rng.choice(["what time is it", "what's the time", "do you have the time", "what time is it now"])
        return ex("info", f"time:{text}", text, [line("get_current_time")], phonebook(rng))
    city = rng.choice(list(B.CITIES))
    text = rng.choice(["what time is it in {c}", "what's the time in {c}", "current time in {c}"]).format(c=city)
    return ex("info", f"time:{text}", text, [line("get_current_time", B.CITIES[city])], phonebook(rng))


def s_convert(rng: random.Random) -> Example:
    a, b = rng.sample(list(B.CITIES), 2)
    hour = rng.randint(1, 12)
    ampm = rng.choice(["am", "pm"])
    text = rng.choice(
        [
            "what is {h} {p} in {a} in {b}",
            "when it's {h} {p} in {a} what time is it in {b}",
            "convert {h} {p} {a} time to {b}",
        ]
    ).format(h=hour, p=ampm, a=a, b=b)
    clock = f"{hour}:00 {ampm.upper()}"
    return ex("info", f"convert:{text}", text, [line("convert_time", B.CITIES[a], clock, B.CITIES[b])], phonebook(rng))


def s_weather(rng: random.Random) -> Example:
    place = rng.choice([None, None, *B.WEATHER_PLACES])
    day = rng.choice(["today", "tomorrow"])
    where = f" in {place}" if place else ""
    when = " tomorrow" if day == "tomorrow" else ""
    text = rng.choice(
        ["what's the weather{w}{d}", "will it rain{w}{d}", "how's the weather{w}{d}", "is it hot{w}{d}"]
    ).format(w=where, d=when)
    args = [place or "", day] if day == "tomorrow" else ([place] if place else [])
    return ex("info", f"weather:{text}", text, [line("weather", *args)], phonebook(rng))


def s_wiki(rng: random.Random) -> Example:
    topic = rng.choice(B.WIKI_TOPICS)
    text = rng.choice(B.WIKI_TEMPLATES).format(t=topic)
    return ex("info", f"wiki:{text}", text, [line("wiki_summary", topic)], phonebook(rng))


def s_pi(rng: random.Random) -> Example:
    pick = rng.choice(["get", "set", "status"])
    if pick == "get":
        text = rng.choice(["how loud is the speaker", "what's the speaker volume", "what volume is the speaker at"])
        return ex("pi", f"pi:{text}", text, [line("get_volume")], phonebook(rng))
    if pick == "set":
        n = rng.randrange(10, 101, 5)
        text = rng.choice(
            ["set the speaker to {n} percent", "speaker volume {n} percent", "make the speaker {n} percent"]
        ).format(n=spoken_number(n, rng))
        return ex("pi", f"pi:{text}:{n}", text, [line("set_volume", str(n))], phonebook(rng))
    text = rng.choice(
        [
            "how is the speaker doing",
            "is the speaker connected",
            "how much memory does the pi have left",
            "what's the pi temperature",
            "how hot is the speaker",
            "how hot is the pi",
            "what is the temperature of the pi",
            "pi temperature",
            "what's the cpu temperature",
            "is the speaker online",
            "how long has the speaker been on",
            "what's the speaker's uptime",
            "how much memory is free on the speaker",
            "check the speaker status",
            "is the pi running okay",
        ]
    )
    return ex("pi", f"pi:{text}", text, [line("device_status")], phonebook(rng))


def s_chat(rng: random.Random) -> Example:
    text, reply = rng.choice(list(B.CHAT.items()))
    return ex("chat", f"chat:{text}", text, [line("none", reply)], phonebook(rng))


def s_ask(rng: random.Random) -> Example:
    text, question = rng.choice(list(B.ASK.items()))
    return ex("ask", f"ask:{text}", text, [line("none", question)], phonebook(rng))


def s_defer(rng: random.Random) -> Example:
    why = rng.choices(["tools", "code", "workflow", "reasoning", "other"], weights=[4, 2, 3, 3, 2])[0]
    text = rng.choice(B.DEFER[why])
    return ex("defer", f"defer:{why}:{text}", text, [line("escalate", why)], phonebook(rng))


def s_defer_compose(rng: random.Random) -> Example:
    why = rng.choice(["tools", "tools", "code", "workflow"])
    template = rng.choice(B.DEFER_COMPOSE[why])
    text = template.format(
        folder=rng.choice(B.FOLDERS),
        topic=rng.choice(B.DOC_TOPICS),
        file=rng.choice(B.FILES),
        query=rng.choice(B.WEB_QUERIES),
        project=rng.choice(B.PROJECT_THINGS),
        code=rng.choice(B.CODE_THINGS),
    )
    return ex("defer", f"defer:{why}:{text}", text, [line("escalate", why)], phonebook(rng))


def s_two_calls(rng: random.Random) -> Example:
    a, b = s_noslot(rng), rng.choice([s_noslot, s_slot, s_weather, s_time])(rng)
    if a["target"] == b["target"]:
        return s_two_calls(rng)
    joiner = rng.choice([" and ", " then ", " and also ", ", and "])
    return ex(
        "multi-call",
        f"two:{a['key']}+{b['key']}",
        a["user"] + joiner + b["user"],
        [a["target"], b["target"]],
        a["phonebook"],
    )


def s_follow_up(rng: random.Random) -> Example:
    """A second turn that only makes sense with the first turn in history."""
    kind = rng.choice(["weather-tomorrow", "weather-place", "timer", "time-city", "text-her"])
    if kind == "weather-tomorrow":
        place = rng.choice(B.WEATHER_PLACES)
        first = (f"what's the weather in {place}", [line("weather", place)])
        text, target = (
            rng.choice(["and tomorrow", "what about tomorrow", "how about tomorrow?"]),
            [line("weather", place, "tomorrow")],
        )
    elif kind == "weather-place":
        a, b = rng.sample(B.WEATHER_PLACES, 2)
        first = (f"what's the weather in {a}", [line("weather", a)])
        text, target = rng.choice(["and in {b}", "what about {b}", "how about {b}"]).format(b=b), [line("weather", b)]
    elif kind == "timer":
        d1, d2 = rng.sample(B.DURATIONS, 2)
        first = (f"set a timer for {d1}", [line("TIMER", d1)])
        text, target = rng.choice(["make it {d}", "change it to {d}", "actually {d}"]).format(d=d2), [line("TIMER", d2)]
    elif kind == "time-city":
        a, b = rng.sample(list(B.CITIES), 2)
        first = (f"what time is it in {a}", [line("get_current_time", B.CITIES[a])])
        text, target = (
            rng.choice(["and in {b}", "what about {b}", "how about {b}"]).format(b=b),
            [line("get_current_time", B.CITIES[b])],
        )
    else:
        book = phonebook(rng)
        name = rng.choice(list(book))
        first = (f"call {name}", [line("CALL", name)])
        text, target = (
            rng.choice(["text them instead", "send them a message instead", "actually just message them"]),
            [line("MESSAGE", name)],
        )
        return ex("follow-up", f"follow:{kind}:{name}:{text}", text, target, book, [(first[0], "\n".join(first[1]))])
    return ex(
        "follow-up", f"follow:{kind}:{first[0]}:{text}", text, target, phonebook(rng), [(first[0], "\n".join(first[1]))]
    )


def doc_subject(rng: random.Random) -> str:
    return rng.choice(B.DOC_SUBJECTS + [t.lower() for t in B.WIKI_TOPICS])


def s_doc(rng: random.Random) -> Example:
    """A question about the user's own documents: escalate|tools, never a general-knowledge lookup."""
    subject = doc_subject(rng)
    if rng.random() < 0.2:
        text = rng.choice(B.DOC_BARE).format(s=subject)
    else:
        text = rng.choice(B.DOC_TEMPLATES).format(place=rng.choice(B.DOC_PLACES), s=subject)
    return ex("doc", f"doc:{text}", text, [line("escalate", "tools")], phonebook(rng))


def s_doc_follow_up(rng: random.Random) -> Example:
    a, b = doc_subject(rng), doc_subject(rng)
    first = rng.choice(B.DOC_TEMPLATES).format(place=rng.choice(B.DOC_PLACES), s=a)
    text = rng.choice(B.DOC_FOLLOWUPS).format(s=b)
    return ex(
        "doc",
        f"docfollow:{first}:{text}",
        text,
        [line("escalate", "tools")],
        phonebook(rng),
        [(first, line("escalate", "tools"))],
    )


def s_doc_compound(rng: random.Random) -> Example:
    """A home command and a document question in one turn: the home line, then escalate|tools."""
    name = rng.choice(list(B.HOME_FOR_COMPOUND))
    home = rng.choice(B.HOME_FOR_COMPOUND[name])
    subject = doc_subject(rng)
    doc = rng.choice(B.DOC_TEMPLATES).format(place=rng.choice(B.DOC_PLACES), s=subject)
    joiner = rng.choice([" and ", " then ", " and also ", ", and "])
    if rng.random() < 0.5:
        return ex(
            "doc",
            f"doccompound:{home}:{doc}",
            home + joiner + doc,
            [line(name), line("escalate", "tools")],
            phonebook(rng),
        )
    return ex(
        "doc", f"doccompound:{doc}:{home}", doc + joiner + home, [line("escalate", "tools"), line(name)], phonebook(rng)
    )


def s_wiki_contrast(rng: random.Random) -> Example:
    """The general-knowledge side of the contrast with s_doc: a named topic asked about plainly is a lookup."""
    return s_wiki(rng)


def s_chat_fact(rng: random.Random) -> Example:
    text, reply = rng.choice(list(B.CHAT_FACTS.items()))
    return ex("chat", f"chatfact:{text}", text, [line("none", reply)], phonebook(rng))


def s_ask_more(rng: random.Random) -> Example:
    text, question = rng.choice(list(B.ASK_MORE.items()))
    return ex("ask", f"askmore:{text}", text, [line("none", question)], phonebook(rng))


def s_ask_follow_up(rng: random.Random) -> Example:
    """The user answers the question the router asked: now the call goes out."""
    pool = {**B.ASK, **B.ASK_MORE}
    asks = [(t, q) for t, q in pool.items() if q in B.ASK_FOLLOW]
    first, question = rng.choice(asks)
    tool = B.ASK_FOLLOW[question]
    book = phonebook(rng)
    if tool in ("CALL", "MESSAGE"):
        name = rng.choice(list(book))
        text = rng.choice(
            ["{n}", "call {n}", "{n} please", "it's {n}"] if tool == "CALL" else ["{n}", "{n} please", "message {n}"]
        ).format(n=name)
        target = [line(tool, name)]
    elif tool == "TIMER":
        d = rng.choice(B.DURATIONS)
        text, target = rng.choice(["{d}", "for {d}", "make it {d}"]).format(d=d), [line("TIMER", d)]
    else:
        hour, minute, ampm = rng.randint(1, 12), rng.choice([0, 15, 30, 45]), rng.choice(["AM", "PM"])
        text = rng.choice(["{t}", "at {t}", "for {t}"]).format(t=time_forms(hour, minute, ampm, rng))
        target = [line("ALARM", f"{hour}:{minute:02d} {ampm}")]
    return ex("follow-up", f"askfollow:{first}:{text}", text, target, book, [(first, line("none", question))])


def s_defer_near_home(rng: random.Random) -> Example:
    text = rng.choice(B.DEFER_NEAR_HOME)
    return ex("defer", f"nearhome:{text}", text, [line("escalate", "tools")], phonebook(rng))


def s_web_answer(rng: random.Random) -> Example:
    """A question that needs current information: the router passes the complete question to web_answer."""
    family = rng.choices(
        ["office", "champion", "price", "currency", "product", "upcoming", "happened", "fact"],
        weights=[4, 3, 3, 2, 3, 2, 5, 4],
    )[0]
    if family == "office":
        office = rng.choice(B.OFFICES)
        place = rng.choice(B.PLACES_OFFICE[office])
        said, query = rng.choice(B.OFFICE_TEMPLATES)
        if not place:  # a global office: drop the "of ..." and its place
            said, query = "who is the current secretary general of the united nations", None
            text, question = said, "Who is the current Secretary-General of the United Nations?"
        else:
            text, question = said.format(o=office, p=place), query.format(o=office, p=place)
    elif family == "champion":
        event = rng.choice(B.EVENTS_CHAMP)
        said, query = rng.choice(B.CHAMP_TEMPLATES)
        text, question = said.format(e=event), query.format(e=event)
    elif family == "price":
        asset = rng.choice(B.ASSETS)
        said, query = rng.choice(B.PRICE_TEMPLATES)
        text, question = said.format(a=asset), query.format(a=asset)
    elif family == "currency":
        a, b, name_a, name_b = rng.choice(B.CURRENCIES)
        said, query = rng.choice(B.CURRENCY_TEMPLATES)
        text, question = said.format(a=a, b=b, na=name_a, nb=name_b), query.format(a=a, b=b, na=name_a, nb=name_b)
    elif family == "product":
        product = rng.choice(B.PRODUCTS)
        said, query = rng.choice(B.PRODUCT_TEMPLATES)
        text, question = said.format(p=product), query.format(p=product)
    elif family == "upcoming":
        text, question = rng.choice(B.UPCOMING)
    elif family == "happened":
        text, question = rng.choice(B.HAPPENED)
    else:
        fact, question = rng.choice(B.WEB_FACTS)
        text = rng.choice(B.WEB_FACT_TEMPLATES).format(f=fact)
    return ex("web", f"web:{text}", text, [line("web_answer", question)], phonebook(rng))


def s_settled(rng: random.Random) -> Example:
    """Hard negative: a settled historical fact is a topic lookup, whatever the time words say."""
    text, topic = rng.choice(B.SETTLED)
    return ex("info", f"settled:{text}", text, [line("wiki_summary", topic)], phonebook(rng))


def s_now_variants(rng: random.Random) -> Example:
    """Hard negatives: weather, clock and home requests that carry 'now' or 'today' keep their own tool."""
    pick = rng.choice(["weather", "weather-place", "time", "time-city", "home"])
    if pick == "weather":
        text = rng.choice(B.WEATHER_NOW)
        return ex("info", f"now:{text}", text, [line("weather")], phonebook(rng))
    if pick == "weather-place":
        place = rng.choice(B.WEATHER_PLACES)
        text = rng.choice(
            ["what's the weather in {p} right now", "is it raining in {p} now", "how's {p} weather today"]
        ).format(p=place)
        return ex("info", f"now:{text}", text, [line("weather", place)], phonebook(rng))
    if pick == "time":
        text = rng.choice(B.TIME_NOW)
        return ex("info", f"now:{text}", text, [line("get_current_time")], phonebook(rng))
    if pick == "time-city":
        city = rng.choice(list(B.CITIES))
        text = rng.choice(
            ["what time is it in {c} right now", "what's the time in {c} now", "current time in {c} right now"]
        ).format(c=city)
        return ex("info", f"now:{text}", text, [line("get_current_time", B.CITIES[city])], phonebook(rng))
    base = rng.choice([s_noslot, s_slot])(rng)
    suffix = rng.choice(B.NOW_SUFFIXES)
    text = f"{suffix} {base['user']}" if rng.random() < 0.3 else f"{base['user']} {suffix}"
    return ex("home", f"now:{text}", text, [base["target"]], base["phonebook"])


def s_arithmetic(rng: random.Random) -> Example:
    """Spoken arithmetic: reasoning for the hosted model. Digits or number words; never a home action."""
    template = rng.choice(B.ARITH_TEMPLATES)
    a, b = rng.randint(2, 99), rng.randint(2, 99)
    if "divide" in template:
        b = rng.randint(2, 12)
        a = b * rng.randint(2, 40)
    root = rng.randint(2, 15)

    def say(n: int) -> str:
        return words(n) if n < 100 and rng.random() < 0.4 else str(n)

    text = template.format(
        a=say(a),
        b=say(b),
        p=say(rng.choice([5, 10, 15, 20, 25, 30, 40, 50, 75])),
        n=say(rng.choice([40, 80, 120, 200, 240, 360, 500, 1000])),
        sq=say(root * root),
        c=say(rng.randint(2, 9)),
    )
    return ex("arith", f"arith:{text}", text, [line("escalate", "reasoning")], phonebook(rng))


# ------------------------------------------------------------------------------------------ ticket 03: history
SLOT_ACTIONS = {"TEMPERATURE", "TIMER", "ALARM", "BRIGHTNESS", "set_volume"}


def pairs_of(e: Example) -> list[tuple[str, str]]:
    """An example as the (user, assistant) exchanges it leaves in a history."""
    return [*e["history"], (e["user"], e["target"])]


def last_name(pairs: list[tuple[str, str]]) -> str:
    return T.parse_line(pairs[-1][1].splitlines()[-1]).name


def slot_action(rng: random.Random) -> Example:
    for _ in range(60):
        e = s_slot(rng)
        if T.parse_line(e["target"]).name in SLOT_ACTIONS:
            break
    return e


def earlier_exchanges(rng: random.Random, slot_last: bool = False) -> list[tuple[str, str]]:
    """1-3 earlier turns of any kind; with slot_last the last one is a slot action (a number was just set)."""
    n = rng.choices([1, 2, 3], [5, 3, 2])[0]
    pairs: list[tuple[str, str]] = []
    for i in range(n):
        if slot_last and i == n - 1:
            maker = rng.choice([slot_action, slot_action, s_ask_follow_up, lambda r: pi_turn(r, "set")])
        else:
            maker = rng.choices([m for _, m in HISTORY_MAKERS], [w for w, _ in HISTORY_MAKERS])[0]
        pairs += pairs_of(maker(rng))
    return pairs


def s_topic_switch(rng: random.Random) -> Example:
    """An independent request after an earlier action: the target is what the request has without any history."""
    with shared_book(rng) as book:
        for _ in range(60):
            pairs = earlier_exchanges(rng, slot_last=rng.random() < 0.55)
            final = rng.choices([m for _, m in SWITCH_FINALS], [w for w, _ in SWITCH_FINALS])[0](rng)
            if final["user"] in {u for u, _ in pairs} or final["target"] == pairs[-1][1].splitlines()[-1]:
                continue
            if last_name(pairs) in SLOT_ACTIONS and T.parse_line(final["target"].splitlines()[0]).name == last_name(
                pairs
            ):
                continue
            break
    return ex("switch", f"switch:{final['key']}", final["user"], [final["target"]], book, pairs)


def s_ack_after_anything(rng: random.Random) -> Example:
    """A pleasantry or a dismissal after any history is a short reply; as the answer to a question it stays a call."""
    with shared_book(rng) as book:
        if rng.random() < 0.2:
            e = s_ask_follow_up(rng)
            prefix = rng.choice(B.ACK_PREFIXES)
            return ex(
                "ack-answer",
                f"ackanswer:{prefix}:{e['key']}",
                f"{prefix} {e['user']}",
                [e["target"]],
                book,
                e["history"],
            )
        phrase, tone = rng.choice(B.ACKS)
        if tone == "dismiss" and rng.random() < 0.35:
            asked = s_ask(rng)
            pairs = pairs_of(asked)
        else:
            pairs = earlier_exchanges(rng)
    reply = rng.choice(B.ACK_REPLIES[tone])
    return ex("ack", f"ack:{phrase}:{pairs[-1][0]}", phrase, [line("none", reply)], book, pairs)


def s_action_question(rng: random.Random) -> Example:
    """A question about something already done is not a request to do it."""
    with shared_book(rng) as book:
        name = rng.choice(list(book) if rng.random() < 0.75 else B.NOT_IN_BOOK)
        text = rng.choice(B.ACTION_QUESTIONS).format(n=name)
        pairs = earlier_exchanges(rng) if rng.random() < 0.5 else []
    return ex("actionq", f"actionq:{text}", text, [line("escalate", "other")], book, pairs)


GARBLE_VOCAB = sorted({w for g in B.GARBLES for w in g.split()})


def s_garble(rng: random.Random) -> Example:
    """A fragment with no intent: ask the user to repeat, never guess a command."""
    if rng.random() < 0.5:
        text = rng.choice(B.GARBLES)
    else:
        text = " ".join(rng.choice(GARBLE_VOCAB) for _ in range(rng.randint(1, 3)))
    with shared_book(rng) as book:
        pairs = earlier_exchanges(rng) if rng.random() < 0.4 else []
    return ex("garble", f"garble:{text}", text, [line("none", B.GARBLE_REPLY)], book, pairs)


def s_short_command(rng: random.Random) -> Example:
    """Contrast for the garbles: a real command of one to three words stays a command."""
    shorts = [(n, t) for n, texts in B.NO_SLOT.items() for t in texts if len(t.split()) <= 3]
    name, text = rng.choice(shorts)
    with shared_book(rng) as book:
        pairs = earlier_exchanges(rng) if rng.random() < 0.3 else []
    return ex("short-command", f"short:{text}", text, [line(name)], book, pairs)


def pi_turn(rng: random.Random, kind: str | None = None) -> Example:
    """One turn about the speaker's Pi: set the volume, read it, health, or the network (hosted model)."""
    kind = kind or rng.choice(["set", "set", "get", "status", "status", "network"])
    alias = rng.choice(B.PI_ALIASES)
    speaker = rng.choice(["the speaker", f"the {alias}", f"the {alias} speaker"])
    if kind == "set":
        n = rng.randrange(10, 101, 5)
        thing = rng.choice(
            ["the speaker", "the volume", "the speaker volume", f"the {alias}'s volume", f"{alias} volume"]
        )
        text = rng.choice(B.PI_SET).format(t=thing, n=spoken_number(n, rng))
        return ex("pi-contrast", f"pic:{text}", text, [line("set_volume", str(n))], phonebook(rng))
    if kind == "get":
        text = rng.choice(B.PI_GET).format(t=speaker)
        return ex("pi-contrast", f"pic:{text}", text, [line("get_volume")], phonebook(rng))
    if kind == "status":
        text = rng.choice(B.PI_STATUS + B.PI_STATUS_RAW).format(t=speaker, a=alias)
        return ex("pi-contrast", f"pic:{text}", text, [line("device_status")], phonebook(rng))
    text = rng.choice(B.PI_NETWORK).format(t=speaker)
    return ex("pi-contrast", f"pic:{text}", text, [line("escalate", "tools")], phonebook(rng))


def s_pi_contrast(rng: random.Random) -> Example:
    """The three Pi tools and the network request stay apart, alone and after other Pi or home turns."""
    with shared_book(rng) as book:
        roll = rng.random()
        if roll < 0.35:
            pairs: list[tuple[str, str]] = []
            final = pi_turn(rng)
        elif roll < 0.5:  # a status question, then a set: the set must not be read as status
            pairs = pairs_of(pi_turn(rng, "status"))
            final = pi_turn(rng, "set")
        elif roll < 0.6:  # the same set again and again
            pairs = pairs_of(pi_turn(rng, "set")) + pairs_of(pi_turn(rng, "set"))
            final = pi_turn(rng, "set")
        elif roll < 0.8:  # some Pi turns, then any Pi turn
            pairs = []
            for _ in range(rng.randint(1, 3)):
                pairs += pairs_of(pi_turn(rng))
            final = pi_turn(rng)
        else:  # home turns, then a Pi turn
            pairs = earlier_exchanges(rng, slot_last=rng.random() < 0.5)
            final = pi_turn(rng)
    return ex("pi-contrast", final["key"], final["user"], [final["target"]], book, pairs)


def delegate_request(rng: random.Random, family: str) -> tuple[str, str]:
    """(what the user says, the escalate reason) for a request that belongs to the hosted model's delegate tools."""
    if family == "build":
        return rng.choice(B.DELEGATE_BUILD).format(thing=rng.choice(B.DELEGATE_THINGS)), "code"
    if family == "run":
        text = rng.choice(B.DELEGATE_RUN).format(cmd=rng.choice(B.DELEGATE_COMMANDS), what=rng.choice(B.DELEGATE_WHAT))
        return text, "tools"
    if family == "status":
        return rng.choice(B.DELEGATE_STATUS).format(job=rng.choice(B.DELEGATE_JOBS)), "tools"
    if family == "workflow":
        return rng.choice(B.DELEGATE_WORKFLOW).format(cmd=rng.choice(B.DELEGATE_COMMANDS)), "workflow"
    return rng.choice(B.DELEGATE_NO_JOB), "tools"


def s_delegate_request(rng: random.Random) -> Example:
    """Build, run, check or cancel a job: the hosted model's delegate tools, never a lookup or a home call."""
    family = rng.choices(["build", "run", "status", "workflow", "no-job"], [3, 3, 3, 1, 3])[0]
    with shared_book(rng) as book:
        pairs = earlier_exchanges(rng) if rng.random() < 0.35 else []
        if family == "no-job":  # nothing is named: only the earlier request says what "it" is
            text, reason = delegate_request(rng, rng.choice(["build", "run", "status"]))
            pairs += [(text, line("escalate", reason))]
        text, reason = delegate_request(rng, family)
    return ex("delegate", f"delegate:{text}", text, [line("escalate", reason)], book, pairs)


def s_status_words_web(rng: random.Random) -> Example:
    """Contrast for the delegate seed: a real-world fact that says status, results or done is still a lookup."""
    text, question = rng.choice(B.WEB_STATUS_WORDS)
    with shared_book(rng) as book:
        pairs = earlier_exchanges(rng) if rng.random() < 0.3 else []
    return ex("web-contrast", f"webstatus:{text}", text, [line("web_answer", question)], book, pairs)


def s_statement(rng: random.Random) -> Example:
    """A reaction that is neither a question nor a command gets a short remark, not a lookup or a repeated call."""
    text = rng.choice(B.STATEMENTS)
    with shared_book(rng) as book:
        pairs = earlier_exchanges(rng) if rng.random() < 0.85 else []
    reply = rng.choice(B.STATEMENT_REPLIES)
    return ex(
        "statement", f"statement:{text}:{pairs[-1][0] if pairs else ''}", text, [line("none", reply)], book, pairs
    )


def s_statement_contrast(rng: random.Random) -> Example:
    """Contrast: a bare noun phrase or wh- query without a question mark is still a lookup, after any history."""
    text, tool, arg = rng.choice(B.STATEMENT_CONTRAST)
    with shared_book(rng) as book:
        pairs = earlier_exchanges(rng) if rng.random() < 0.7 else []
    return ex("statement-contrast", f"stcontrast:{text}", text, [line(tool, arg)], book, pairs)


HISTORY_MAKERS: list[tuple[float, Callable[[random.Random], Example]]] = [
    (3, s_noslot),
    (3, s_slot),
    (2, s_contact),
    (2, s_weather),
    (2, s_time),
    (2, s_web_answer),
    (1, s_wiki),
    (1, s_chat),
    (2, s_defer),
    (1, s_arithmetic),
    (2, s_pi),
    (2, s_ask_follow_up),
]
SWITCH_FINALS: list[tuple[float, Callable[[random.Random], Example]]] = [
    (3, s_noslot),
    (3, s_slot),
    (3, s_contact),
    (3, s_contact_missing),
    (2, s_time),
    (2, s_weather),
    (2, s_web_answer),
    (1, s_settled),
    (1, s_arithmetic),
    (2, s_defer),
    (3, s_ask),
    (1, s_ask_more),
    (1, s_chat),
    (2, s_pi),
]

MIX: list[tuple[float, Callable[[random.Random], Example]]] = [
    (14, s_noslot),
    (14, s_slot),
    (6, s_contact),
    (2, s_contact_missing),
    (4, s_time),
    (2, s_convert),
    (5, s_weather),
    (5, s_wiki),
    (6, s_pi),
    (3, s_chat),
    (3, s_ask),
    (5, s_defer),
    (10, s_defer_compose),
    (6, s_two_calls),
    (14, s_follow_up),
    (5, s_doc),
    (3, s_doc_follow_up),
    (3, s_doc_compound),
    (3, s_wiki_contrast),
    (4, s_chat_fact),
    (3, s_ask_more),
    (3, s_ask_follow_up),
    (4, s_defer_near_home),
    (5, s_web_answer),
    (2, s_settled),
    (3, s_now_variants),
    (5, s_arithmetic),
    (8, s_topic_switch),
    (3, s_ack_after_anything),
    (3, s_action_question),
    (2, s_garble),
    (1.5, s_short_command),
    (6, s_pi_contrast),
    (4, s_delegate_request),
    (1.5, s_status_words_web),
    (3, s_statement),
    (1.5, s_statement_contrast),
]


# ------------------------------------------------------------------------------------------ noise
HOMOPHONES = {
    "lights": ["light", "likes"],
    "timer": ["time her"],
    "alarm": ["a larm"],
    "weather": ["whether"],
    "pause": ["paws"],
    "colour": ["color"],
}


def add_noise(text: str, rng: random.Random) -> str:
    """Light speech-recognition style noise: no punctuation or capitals, fillers, repeats, rare mishearings.
    The target is not changed, so only noise that keeps the request recognisable is applied."""
    out = text.lower()
    out = re.sub(r"[?!.,]", "", out)
    words_ = out.split()
    if rng.random() < 0.25:
        words_.insert(rng.randint(0, 1), rng.choice(B.FILLERS))
    if rng.random() < 0.08 and len(words_) > 2:
        i = rng.randrange(len(words_))
        words_.insert(i, words_[i])  # a stutter
    if rng.random() < 0.05:
        for i, w in enumerate(words_):
            if w in HOMOPHONES:
                words_[i] = rng.choice(HOMOPHONES[w])
                break
    return " ".join(words_)


# ------------------------------------------------------------------------------------------ teacher (optional)
def teacher_rewrites(utterances: list[str], model: str, per: int = 2) -> dict[str, list[str]]:
    """Ask a hosted Gemini to say each utterance differently, as a consumer talking to a voice assistant.
    Returns {utterance: [rewrites]}; a rewrite that loses the numbers or names of the original is dropped."""
    from google import genai  # imported here: only needed with --teacher

    key = None
    for folder in ("google-ai-studio",):
        for name in ("api-key.txt", "api_key.txt"):
            path = Path.home() / ".secrets" / folder / name
            if path.exists():
                key = "".join(path.read_text().split())
    if key is None:
        sys.exit("no key in ~/.secrets/google-ai-studio/ (the teacher needs one); run without --teacher")
    client = genai.Client(api_key=key)
    out: dict[str, list[str]] = {}
    for start in range(0, len(utterances), 25):
        batch = utterances[start : start + 25]
        prompt = (
            f"Rewrite each request to a home voice assistant in {per} different natural ways, the way real people talk "
            "(casual, sometimes indirect, speech-to-text style: lowercase, no punctuation, an occasional filler). "
            "Keep every number, name, place, colour and time exactly as meant. "
            "Return JSON: a list of lists of strings, "
            "one inner list per request, in order.\n" + json.dumps(batch)
        )
        try:
            reply = client.models.generate_content(model=model, contents=prompt)
            parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", (reply.text or "").strip(), flags=re.M).strip())
        except Exception as exc:  # noqa: BLE001 - a failed batch just keeps the plain wording
            print(f"teacher batch failed ({type(exc).__name__}); keeping plain wording", file=sys.stderr)
            continue
        for original, options in zip(batch, parsed, strict=False):
            keep = [
                o
                for o in options
                if isinstance(o, str) and set(re.findall(r"\d+", o)) >= set(re.findall(r"\d+", original))
            ]
            if keep:
                out[original] = keep
    return out


# ------------------------------------------------------------------------------------------ split and leakage
def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower())


def grams(text: str, n: int = 3) -> set[tuple[str, ...]]:
    w = norm(text).split()
    return {tuple(w[i : i + n]) for i in range(max(1, len(w) - n + 1))}


def eval_prompts() -> list[str]:
    """Every user sentence of the two evaluation sets."""
    out = []
    for path in (ROOT / "pipeline/config/eval/agent_prompts.jsonl", ROOT / "finetune/eval/onit_eval.jsonl"):
        if path.exists():
            for raw in path.read_text().splitlines():
                if raw.strip():
                    out.extend(json.loads(raw)["turns"])
    return out


def leaks(user: str, eval_grams: list[tuple[str, set]]) -> bool:
    """True when `user` is a near-duplicate of an evaluation prompt (3-gram overlap of 0.8 or more)."""
    g = grams(user)
    for text, eg in eval_grams:
        if norm(user) == norm(text):
            return True
        small = min(len(g), len(eg))
        if small >= 3 and len(g & eg) / small >= 0.8:
            return True
    return False


def split_of(key: str, seed: int) -> str:
    """dev 8%, test 8%, train 84%, decided by the seed key so a template/slot pairing stays in one split."""
    h = int(hashlib.sha256(f"{seed}:{key}".encode()).hexdigest(), 16) % 100
    return "test" if h < 8 else "dev" if h < 16 else "train"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=HERE / "generated")
    ap.add_argument("--n", type=int, default=25000, help="examples to draw (duplicates and leaks are dropped after)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--teacher", choices=("none", "gemini"), default="none")
    ap.add_argument("--teacher-model", default="gemini-3.1-flash-lite")
    ap.add_argument(
        "--include-eval",
        action="store_true",
        help="keep eval-set near-duplicates (ONLY for the final model, after the evaluation)",
    )
    ap.add_argument("--teacher-share", type=float, default=0.5, help="share of distinct utterances sent to the teacher")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    weights = [w for w, _ in MIX]
    makers = [m for _, m in MIX]
    drawn = [rng.choices(makers, weights)[0](rng) for _ in range(a.n)]

    if a.teacher == "gemini":
        distinct = sorted({e["user"] for e in drawn if not e["history"]})
        chosen = [u for u in distinct if rng.random() < a.teacher_share]
        rewrites = teacher_rewrites(chosen, a.teacher_model)
        for e in drawn:
            if e["user"] in rewrites and rng.random() < 0.6:
                e["user"] = rng.choice(rewrites[e["user"]])
                e["key"] += ":teacher"

    eval_g = [(t, grams(t)) for t in eval_prompts()]
    a.out.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, str, str]] = set()
    stats: Counter = Counter()
    files = {s: (a.out / f"{s}.jsonl").open("w") for s in ("train", "dev", "test")}
    for i, e in enumerate(drawn):
        user = add_noise(e["user"], rng) if rng.random() < 0.6 else e["user"]
        sig = (user, json.dumps(e["history"]), e["target"])
        if sig in seen:
            stats["dropped_duplicate"] += 1
            continue
        if not a.include_eval and leaks(user, eval_g):
            stats["dropped_leak"] += 1
            continue
        seen.add(sig)
        for _, lines in e["history"]:
            for ln in lines.splitlines():
                T.parse_line(ln)
        for ln in e["target"].splitlines():
            T.parse_line(ln)  # every label must parse
        split = split_of(e["key"], a.seed)
        messages = build_messages(e["phonebook"], e["history"], user, e["target"])
        files[split].write(
            json.dumps(
                {"id": f"{a.seed}-{i}", "split": split, "kind": e["kind"], "messages": messages, "target": e["target"]},
                ensure_ascii=False,
            )
            + "\n"
        )
        stats[f"{split}"] += 1
        stats[f"kind:{e['kind']}"] += 1
    for f in files.values():
        f.close()
    (a.out / "stats.json").write_text(json.dumps(dict(sorted(stats.items())), indent=1))
    print(json.dumps(dict(sorted(stats.items())), indent=1))


if __name__ == "__main__":
    main()
