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
def phonebook(rng: random.Random) -> dict[str, str]:
    return dict(rng.sample(B.CONTACT_NAMES, rng.randint(4, 7)))


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
            "what's the pi temperature",
            "is the speaker connected",
            "how much memory does the pi have left",
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


MIX: list[tuple[float, Callable[[random.Random], Example]]] = [
    (14, s_noslot),
    (14, s_slot),
    (6, s_contact),
    (2, s_contact_missing),
    (4, s_time),
    (2, s_convert),
    (5, s_weather),
    (5, s_wiki),
    (4, s_pi),
    (3, s_chat),
    (3, s_ask),
    (5, s_defer),
    (10, s_defer_compose),
    (6, s_two_calls),
    (14, s_follow_up),
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
