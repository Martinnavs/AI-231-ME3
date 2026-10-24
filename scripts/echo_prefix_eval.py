"""Would stripping the assistant's own words from the front of an interruption help? (text only)

    uv run --group laptop python scripts/echo_prefix_eval.py out/sessions/agent-interrupt-2 [...]

For every interruption in a recorded session (the turn after a ``barge_in`` row in turns.jsonl),
the logged transcript goes through today's rules (echo text guard, stop phrase, leading-stop
strip) and through the candidate rules first:

* the whole transcript is the assistant's words (a fuzzy in-order run of at least 3 words that is
  at least 60% of what was heard): treat the interruption as a bare stop;
* up to 8 words before the first stop word ("stop", "wait", "pause", "hold on", "hang on"), at
  least one of which (3+ letters) fuzzily matches a word of the reply: drop them, then today's
  rules run. (A first version required half of them to match; it missed "Album published in
  twenty five Stop, ...". Both thresholds were chosen on the sessions this script reads.)

``--fix-pause`` also stops today's leading-stop strip from eating "pause" ("Stop. Pause the
music." -> "The music." in today's code).

Prints what the language model would get (or "STOP" / "IGNORED") before and after. The reply's
words are the logged ``spoken`` text, which is the whole reply, not only the part played.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doc_chat.__main__ import _is_stop_phrase, _strip_stop_prefix  # noqa: E402
from doc_chat.bargein import looks_like_echo  # noqa: E402

_WORD = re.compile(r"[A-Za-z0-9']+")
_STOP_AT = {"stop", "wait", "pause"}


def _norm(w: str) -> str:
    return w.lower().strip("'")


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def _reply_words(spoken: str) -> list[str]:
    return [_norm(w) for w in _WORD.findall(spoken.replace("-", " "))]


def fuzzy_echo(heard: str, spoken: str, min_run: int = 3, share: float = 0.6) -> bool:
    """The whole transcript is (nearly) a run of the reply's words, allowing misheard words."""
    a = [_norm(w) for w in _WORD.findall(heard)]
    b = _reply_words(spoken)
    if len(a) < min_run or not b:
        return False
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if _similar(a[i - 1], b[j - 1]):
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best >= min_run and best / len(a) >= share


def strip_echo_prefix(heard: str, spoken: str, min_hits: int = 1, max_prefix: int = 8) -> str:
    """'2005 border. Stop. Set a timer.' -> 'Stop. Set a timer.' ('2005 border' is the reply's)."""
    words = list(_WORD.finditer(heard))
    k = next(
        (
            i
            for i, m in enumerate(words)
            if _norm(m.group()) in _STOP_AT
            or (
                _norm(m.group()) in ("hold", "hang")
                and i + 1 < len(words)
                and _norm(words[i + 1].group()) == "on"
            )
        ),
        None,
    )
    if not k:  # no stop word, or it is already first
        return heard
    content = [_norm(m.group()) for m in words[:k] if len(m.group()) >= 3]
    if not content or k > max_prefix:
        return heard
    reply = set(_reply_words(spoken))
    hits = sum(any(_similar(w, r) for r in reply) for w in content)
    if hits < min_hits:
        return heard
    return heard[words[k].start() :]


_STOP_PREFIX_NO_PAUSE = re.compile(
    r"^\W*(?:(?:wait|stop|hold on|hang on|okay|ok|please|sorry|actually|hey|um|uh|so)\b\W*)+",
    re.IGNORECASE,
)


def strip_stop_prefix_no_pause(text: str) -> str:
    """Today's `_strip_stop_prefix` minus "pause" in the prefix ("pause the music" is an order)."""
    m = _STOP_PREFIX_NO_PAUSE.match(text)
    if not m:
        return text
    prefix = m.group(0)
    has_stop = re.search(r"\b(wait|stop|hold on|hang on)\b", prefix, re.IGNORECASE)
    punctuated = re.search(r"\b(wait|stop|on)\b\s*[,.!?;:-]", prefix, re.IGNORECASE)
    if not has_stop or not (punctuated or len(re.findall(r"[a-z']+", prefix.lower())) >= 2):
        return text
    rest = text[m.end() :].strip()
    if len(rest.split()) < 2:
        return text
    return rest[0].upper() + rest[1:]


def outcome(text: str, spoken: str, candidate: bool, fix_pause: bool = False) -> str:
    """What happens to an interruption transcript: IGNORED, STOP, or the text the LLM gets."""
    if candidate and fuzzy_echo(text, spoken):
        return "STOP (all echo)"
    if candidate:
        stripped = strip_echo_prefix(text, spoken)
        if stripped != text:  # the user's words start at the stop word: drop it even unpunctuated
            rest = re.sub(r"^\W*(?:(?:stop|wait|hold on|hang on)\b\W*)+", "", stripped, flags=re.I)
            stripped = rest[0].upper() + rest[1:] if len(rest.split()) >= 2 else stripped
        text = stripped
    if looks_like_echo(text, spoken):
        return "IGNORED (echo guard)"
    said = re.sub(r"[^a-z' ]", " ", text.lower()).split()
    if _is_stop_phrase(said, after_interrupt=True):
        return "STOP"
    return strip_stop_prefix_no_pause(text) if fix_pause else _strip_stop_prefix(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--fix-pause", action="store_true", help="candidate also keeps 'pause ...'")
    args = ap.parse_args(argv)
    changed = total = 0
    for s in args.sessions:
        rows = [json.loads(line) for line in (s / "turns.jsonl").read_text().splitlines()]
        print(f"== {s.name}")
        for i, r in enumerate(rows[:-1]):
            if r.get("interrupted") != "barge_in":
                continue
            heard = str(rows[i + 1].get("question") or "")
            spoken = str(r.get("spoken") or "")
            before = outcome(heard, spoken, False)
            after = outcome(heard, spoken, True, args.fix_pause)
            total += 1
            changed += before != after
            mark = "*" if before != after else " "
            print(f" {mark} t{r['turn']:<3} heard {heard!r}")
            print(f"        before: {before!r}")
            if before != after:
                print(f"        after:  {after!r}")
    print(f"\n{changed} of {total} interruption transcripts change under the candidate rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
