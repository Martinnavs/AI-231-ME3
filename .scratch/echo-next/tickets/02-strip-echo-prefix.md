# Ticket 02: drop the assistant's own words from the front of an interruption (--strip-echo-prefix)

**Status:** delivered-for-review
**Date:** 2026-10-24
**Blocked by:** ticket 01 (edits the same `__main__.py`; run in order)

After a barge-in the recogniser gets 1.2 s of pre-roll, so the assistant's last words often come before the user's "stop" ("2005 border. Stop. Set a timer for ten minutes."). The stop rules then miss the stop and the language model gets the echo (spurious STOP commands, a second timer). Behind a flag, off by default: two helpers in `bargein.py` and their use right after the existing echo text guard in `run_mic`. Evaluated offline on the logged transcripts (`.scratch/echo-next/PLAN.md` E3).

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `src/doc_chat/bargein.py` | edit (list below) | **425** (now 352; exactly 425 after the edits) |
| `src/doc_chat/__main__.py` | edit (list below) | **1344** (now 1326; exactly 1344 after the edits) |
| `tests/doc_chat/test_echo_prefix.py` | new, verbatim | **158** |

**Non-goals / touch nothing else:** no change to `looks_like_echo`, the stop rules or the pre-roll; nothing happens without `--strip-echo-prefix`; no other file.

**Security-sensitive:** no (local text and audio logic; no secrets, no network).

## Edits

Apply in this order. Each OLD block occurs exactly once in the file as it is at that moment; replace it with NEW, byte-for-byte.

### Edit 1: `src/doc_chat/bargein.py`

OLD:

````python
    return match.size >= min_words and match.size / len(a) >= share


class SileroOnset:
````

NEW:

````python
    return match.size >= min_words and match.size / len(a) >= share


_TOKEN = re.compile(r"[A-Za-z0-9']+")
_STOP_AT = {"stop", "wait", "pause"}
_LEADING_STOP = re.compile(r"^\W*(?:(?:stop|wait|hold on|hang on)\b\W*)+", re.IGNORECASE)


def _similar(a: str, b: str) -> bool:
    """The same word, or a misheard one ("mccartney" for "mccarthy"): 4+ letters, 75% alike."""
    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def _reply_words(spoken: str) -> list[str]:
    return [w.lower().strip("'") for w in _TOKEN.findall(spoken.replace("-", " "))]


def is_all_echo(heard: str, spoken: str, min_run: int = 3, share: float = 0.6) -> bool:
    """The whole transcript is a run of the assistant's words, misheard words allowed.

    `looks_like_echo` needs exact words and 80%; the recogniser mishears the echo ("Novel
    published in 2007." for "novel published in 2006"), so this allows near words and 60%: an
    in-order run of at least ``min_run`` near words that is at least ``share`` of what was heard.
    """
    a = [w.lower().strip("'") for w in _TOKEN.findall(heard)]
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


def strip_echo_prefix(heard: str, spoken: str, max_prefix: int = 8) -> str:
    """'2005 border. Stop. Set a timer.' -> 'Set a timer.' when '2005 border' is the assistant's.

    After a barge-in the recogniser gets the pre-roll, so the assistant's last words often come
    before the user's "stop". If at most ``max_prefix`` words come before the first stop word
    ("stop", "wait", "pause", "hold on", "hang on") and one of them (3+ letters) is near a word
    of the reply, they are dropped, and so are the stop words when at least two words remain.
    Otherwise the text comes back unchanged.
    """
    words = list(_TOKEN.finditer(heard))
    k = next(
        (
            i
            for i, m in enumerate(words)
            if m.group().lower() in _STOP_AT
            or (
                m.group().lower() in ("hold", "hang")
                and i + 1 < len(words)
                and words[i + 1].group().lower() == "on"
            )
        ),
        0,
    )
    if k == 0 or k > max_prefix:
        return heard
    reply = set(_reply_words(spoken))
    content = [m.group().lower() for m in words[:k] if len(m.group()) >= 3]
    if not any(_similar(w, r) for w in content for r in reply):
        return heard
    rest = heard[words[k].start() :]
    after = _LEADING_STOP.sub("", rest)
    return after[0].upper() + after[1:] if len(after.split()) >= 2 else rest


class SileroOnset:
````

### Edit 2: `src/doc_chat/__main__.py`

OLD:

````python
    PreRoll,
    SileroOnset,
    looks_like_echo,
)
````

NEW:

````python
    PreRoll,
    SileroOnset,
    is_all_echo,
    looks_like_echo,
    strip_echo_prefix,
)
````

### Edit 3: `src/doc_chat/__main__.py`

OLD:

````python
            after_interrupt = interruption.kind == "barge_in"
            if (
                interruption.kind == "barge_in"
                and getattr(self.args, "echo_text_guard", True)
                and looks_like_echo(heard.text, spoken)
            ):
````

NEW:

````python
            after_interrupt = interruption.kind == "barge_in"
            strip = interruption.kind == "barge_in" and getattr(
                self.args, "strip_echo_prefix", False
            )
            if (
                interruption.kind == "barge_in"
                and getattr(self.args, "echo_text_guard", True)
                and (
                    looks_like_echo(heard.text, spoken)
                    or (strip and is_all_echo(heard.text, spoken))
                )
            ):
````

### Edit 4: `src/doc_chat/__main__.py`

OLD:

````python
            if interruption.kind == "resumed":
                idx -= 1  # the cut-off turn and what followed are one turn
````

NEW:

````python
            if strip and (cleaned := strip_echo_prefix(heard.text, spoken)) != heard.text:
                print(f"      (my own words dropped: {cleaned!r})", flush=True)
                heard = dc_replace(heard, text=cleaned)
            if interruption.kind == "resumed":
                idx -= 1  # the cut-off turn and what followed are one turn
````

### Edit 5: `src/doc_chat/__main__.py`

OLD:

````python
    parser.add_argument(
        "--barge-echo-margin",
````

NEW:

````python
    parser.add_argument(
        "--strip-echo-prefix",
        action="store_true",
        help="after a barge-in, drop the assistant's own words heard before your 'stop' and treat "
        "a transcript made only of its words (misheard ones too) as its echo "
        "(.scratch/echo-next/PLAN.md, E3)",
    )
    parser.add_argument(
        "--barge-echo-margin",
````

## New test file(s), verbatim

### `tests/doc_chat/test_echo_prefix.py`

````python
"""The assistant's own words in front of an interruption (.scratch/echo-next/PLAN.md, E3).

The cases are transcripts logged in the A/B sessions of 2026-10-23 (docs/LIVE-AGENT.md).
"""

import argparse
import asyncio
import io
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from doc_chat.__main__ import Interruption, Session  # noqa: E402
from doc_chat.bargein import is_all_echo, strip_echo_prefix  # noqa: E402
from doc_chat.dialogue import ChatHistory  # noqa: E402
from doc_chat.speech import HeardTurn  # noqa: E402

PRETTY = "All the Pretty Horses is the first book in The Border Trilogy, published in 1992."
NO_COUNTRY = "No Country for Old Men is a 2005 border thriller that has become a classic."
BORN = "Cormac McCarthy was born Charles Joseph McCarthy Jr. in 1933 in Providence."
OUTER = "Outer Dark was Cormac McCarthy's second novel, published in 1968."
PASSENGER = "The Passenger is an interconnected novel published in 2022 about Bobby Western."
ROAD = "The Road is a post-apocalyptic novel published in 2006 about a father and son."
TIMER = "OK. I have stopped the music and set a timer for ten minutes."


def test_its_words_before_the_stop_are_dropped_with_the_stop() -> None:
    assert strip_echo_prefix(
        "First book in the Border Trilogy. Stop! What year was it published?", PRETTY
    ) == ("What year was it published?")
    assert strip_echo_prefix("2005 border. Stop. Set a timer for ten minutes.", NO_COUNTRY) == (
        "Set a timer for ten minutes."
    )
    assert strip_echo_prefix("Joseph McCartney Chief Stop, when did he die?", BORN) == (
        "When did he die?"
    )
    assert strip_echo_prefix("Second, novel stop pause the music.", OUTER) == "Pause the music."
    assert strip_echo_prefix(
        "Album published in twenty five Stop, Play Some Music.", PASSENGER
    ) == ("Play Some Music.")


def test_a_bare_stop_after_its_words_is_kept_for_the_stop_rules() -> None:
    assert strip_echo_prefix("Apocalyptic. Stop.", ROAD) == "Stop."


def test_what_the_user_says_is_left_alone() -> None:
    for text in (
        "Stop. What year was it published?",  # the stop comes first: nothing in front of it
        "Please stop playing music.",  # "please" is not the assistant's word
        "Set a timer for ten minutes.",  # no stop word
        "Tell me about the road and why the father keeps going on and on, stop.",  # too long
    ):
        assert strip_echo_prefix(text, ROAD) == text


def test_a_transcript_of_only_its_words_is_its_echo() -> None:
    assert is_all_echo("Novel published in 2007.", ROAD)  # one word misheard
    assert is_all_echo("Set a timer for a minute.", TIMER)  # it heard its own confirmation
    assert not is_all_echo("When was The Road published?", ROAD)  # its words, not its order
    assert not is_all_echo("Stop.", ROAD)  # too short to tell
    assert not is_all_echo("Turn on the lights.", ROAD)


def _session(**args: Any) -> tuple[Session, list[HeardTurn], list[str]]:
    heard: list[HeardTurn] = []
    answered: list[str] = []

    class Hub:
        async def blocks(self) -> Any:
            while True:
                await asyncio.sleep(3600)
                yield b""

    class Client:
        hub = Hub()
        noise_floor = 0.01

        async def listen_turn(self, **kw: Any) -> HeardTurn:
            audio = kw.get("audio")
            if audio is not None:
                got = 0
                async for _ in audio:
                    got += 1
                    if got >= 3:
                        break
            return heard.pop(0)

    ns = argparse.Namespace(
        play=False, voice="F1", slot="doc?", risk=None, vad_hold=0.6, barge_in=True,
        ask_to_add=False, **args,
    )  # fmt: skip
    s = Session(
        args=ns,
        client=Client(),  # type: ignore[arg-type]
        log_file=io.StringIO(),
        out_dir=None,
        history=ChatHistory(4),
    )

    async def respond(idx: int, turn: HeardTurn) -> Interruption | None:
        answered.append(turn.text)
        if len(answered) == 1:  # the first answer is talked over
            s._spoken = [NO_COUNTRY]
            return Interruption("barge_in", [bytes(3200)] * 3, cancel_ms=0.0, after_final_ms=0.0)
        return None

    s.respond = respond  # type: ignore[method-assign]
    return s, heard, answered


def _turn(text: str) -> HeardTurn:
    return HeardTurn(text, None, None, 1.0, 2.0, 1.5, b"", "", b"")


async def _run(s: Session) -> None:
    await asyncio.wait_for(s.run_mic(), timeout=10)


async def test_with_the_flag_the_command_reaches_the_model_without_its_words() -> None:
    s, heard, answered = _session(strip_echo_prefix=True)
    heard.extend(
        [
            _turn("Tell me about No Country for Old Men."),
            _turn("2005 border. Stop. Set a timer for ten minutes."),
            _turn("Quit."),
        ]
    )
    await _run(s)
    assert answered == ["Tell me about No Country for Old Men.", "Set a timer for ten minutes."]


async def test_without_the_flag_nothing_changes() -> None:
    s, heard, answered = _session()
    heard.extend(
        [
            _turn("Tell me about No Country for Old Men."),
            _turn("2005 border. Stop. Set a timer for ten minutes."),
            _turn("Quit."),
        ]
    )
    await _run(s)
    assert answered[1] == "2005 border. Stop. Set a timer for ten minutes."


async def test_with_the_flag_an_interruption_of_only_its_words_is_ignored() -> None:
    s, heard, answered = _session(strip_echo_prefix=True)
    heard.extend(
        [
            _turn("Tell me about No Country for Old Men."),
            _turn("Two thousand five border thriller that has become."),
            _turn("Quit."),
        ]
    )
    await _run(s)
    assert answered == ["Tell me about No Country for Old Men."]
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `uv run --offline --group laptop ruff check src/doc_chat/bargein.py src/doc_chat/__main__.py tests/doc_chat/test_echo_prefix.py` | All checks passed! |
| 2 | `uv run --offline --group laptop ruff format --check src/doc_chat/bargein.py src/doc_chat/__main__.py tests/doc_chat/test_echo_prefix.py` | 3 files already formatted |
| 3 | `uv run --offline --group laptop mypy` | Success: no issues found in 54 source files |
| 4 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_echo_prefix.py` | 7 passed |
| 5 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_bargein.py` | all passed, 0 failed (count printed) |

## Deviations

none

## Execution Log

### 2026-10-24 agy: DONE
- Proof: 1 ✅ `uv run --offline --group laptop ruff check src/doc_chat/bargein.py src/doc_chat/__main__.py tests/doc_chat/test_echo_prefix.py` (All checks passed!) 2 ✅ `uv run --offline --group laptop ruff format --check src/doc_chat/bargein.py src/doc_chat/__main__.py tests/doc_chat/test_echo_prefix.py` (3 files already formatted) 3 ✅ `uv run --offline --group laptop mypy` (Success: no issues found in 54 source files) 4 ✅ `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_echo_prefix.py` (7 passed in 0.26s) 5 ✅ `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_bargein.py` (46 passed in 1.79s)
- Files: src/doc_chat/bargein.py (425 / 425)
- Files: src/doc_chat/__main__.py (1344 / 1344)
- Files: tests/doc_chat/test_echo_prefix.py (158 / 158)
- Deviations: none
- Notes for reviewer: none



### 2026-10-24 reviewer (assistant): ACCEPTED
- `diff -rq orig work`: only files in the four tickets' tables (and the tickets) changed.
- `cmp` against a freshly built reference (`build_overlay.sh --no-gate`): every file byte-identical.
- Integrated into the repository (backup `.scratch/echo-next/backup-125013/`); repository gate: ruff and mypy clean, pytest 616 passed, 9 skipped, 2 xfailed, 1 failed (the known `test_flow::test_llm_echo_is_accepted_without_documents`).
