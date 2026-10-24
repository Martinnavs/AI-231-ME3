# Ticket 03: doc_chat fillers: stall, tool line, notice and give-up (R2)

**Status:** ready
**Date:** 2026-10-24
**Blocked by:** ticket 02 (this client reads its `{"event": "tool"}` message)

With `--fillers`, doc_chat plays short pre-recorded lines while a slow answer waits, timed from when the transcript is ready: a stall line at 1.2 s, the tool's line when a tool event arrives first, an honest notice at 2.5 s, and at 8 s it gives up with an apology and drops the turn. Nothing plays once the answer has started. The phrases were reviewed by the owner (`.scratch/latency/fillers-draft.md`); their audio is synthesised once at start. Fillers stay out of the chat history.

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `src/doc_chat/__main__.py` | edit (list below) | **1482** (now 1371) |
| `src/doc_chat/agent_client.py` | edit (list below) | **76** (now 71) |
| `src/doc_chat/fillers.py` | new, verbatim | **108** |
| `config/fillers.json` | new, verbatim | **124** |
| `tests/doc_chat/test_fillers.py` | new, verbatim | **72** |
| `tests/doc_chat/test_fillers_session.py` | new, verbatim | **97** |

**Non-goals / touch nothing else:** no change to `pipeline/` (tickets 01-02), `pyproject.toml` or `uv.lock`; no new phrases; never start the speech server, the agent server or a live session.

**Security-sensitive:** no secrets.

## Edits

Apply in this order. Each OLD block occurs exactly once in the file as it is at that moment; replace it with NEW, byte-for-byte.

### Edit 1: `src/doc_chat/__main__.py`

OLD:

````python
from doc_chat.dialogue import ChatHistory, Clarification, clarification_for, parse_yes_no
from doc_chat.speech import (
````

NEW:

````python
from doc_chat.dialogue import ChatHistory, Clarification, clarification_for, parse_yes_no
from doc_chat.fillers import Fillers, TurnFillers
from doc_chat.speech import (
````

### Edit 2: `src/doc_chat/__main__.py`

OLD:

````python
    _prompt_count: int = 0
````

NEW:

````python
    _prompt_count: int = 0
    fillers: Fillers | None = None  # --fillers: pre-recorded lines while a slow answer waits
    _turn_fillers: TurnFillers | None = None
    _filler_push: Callable[[bytes], None] | None = None  # into the answer's player
    _filler_started: float | None = None  # perf_counter of the first filler audio
    _filler_lines: list[str] = field(default_factory=list)
    _loop: asyncio.AbstractEventLoop | None = None
````

### Edit 3: `src/doc_chat/__main__.py`

OLD:

````python
        finally:
            await asyncio.to_thread(player.close)

    async def complete_turn(self, heard: HeardTurn) -> HeardTurn:
````

NEW:

````python
        finally:
            await asyncio.to_thread(player.close)

    def _play_filler(self, kind: str) -> None:
        """Queue one filler line on the answer's player (before any answer audio)."""
        picked = self.fillers.pick(kind) if self.fillers is not None else None
        if picked is None or self._filler_push is None or self._answer_started is not None:
            return
        text, pcm = picked
        if self._filler_started is None:
            self._filler_started = time.perf_counter()
        self._filler_lines.append(text)
        print(f"bot:  ({text})", flush=True)
        self._filler_push(pcm)

    async def _filler_timer(self, t_final: float) -> str:
        """Play this turn's timed fillers; returns "giveup" when no answer came in time."""
        turn = self._turn_fillers
        while turn is not None and (at := turn.next_at()) is not None:
            await asyncio.sleep(max(0.0, t_final + at - time.perf_counter()))
            if self._answer_started is not None:
                break
            kind = turn.due(time.perf_counter() - t_final)
            if kind == "giveup":
                return kind
            if kind is not None:
                self._play_filler(kind)
        await asyncio.Future()  # nothing more to play; cancelled when the turn ends
        return ""

    def on_agent_event(self, event: dict[str, Any]) -> None:
        """A message from the agent server (any thread): a tool call may play its filler."""
        if self._loop is not None and event.get("event") == "tool":
            self._loop.call_soon_threadsafe(self._tool_filler, str(event.get("name", "")))

    def _tool_filler(self, name: str) -> None:
        turn = self._turn_fillers
        if turn is not None and self._answer_started is None:
            kind = turn.tool(name)
            if kind is not None:
                self._play_filler(kind)

    async def _give_up(self, idx: int, heard: HeardTurn, work: asyncio.Task[None]) -> None:
        """No answer in time: drop the turn and apologise (the user asks again)."""
        if self._stream is not None:
            self._stream.cancel()
        if self._player is not None:
            self._player.abort()
        await self.client.send_tts_cancel()
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(work, timeout=2.0)
        print("  [gave up: no answer in time]", flush=True)
        picked = self.fillers.pick("giveup") if self.fillers is not None else None
        if picked is not None:
            await self.play_pcm(*picked)
        self.log(
            {
                "turn": idx,
                "question": heard.text,
                "answer_words": 0,
                "gave_up": True,
                "fillers": list(self._filler_lines) or None,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )

    async def complete_turn(self, heard: HeardTurn) -> HeardTurn:
````

### Edit 4: `src/doc_chat/__main__.py`

OLD:

````python
        self._player = player
        on_chunk = self._chunk(player)
````

NEW:

````python
        self._player = player
        self._filler_push = player.push if player is not None else None
        on_chunk = self._chunk(player)
````

### Edit 5: `src/doc_chat/__main__.py`

OLD:

````python
            "cue_s": self._cue_s,
        }
````

NEW:

````python
            "cue_s": self._cue_s,
            "fillers": list(self._filler_lines) or None,
            "filler_after_final_ms": (
                None
                if self._filler_started is None or heard is None
                else round((self._filler_started - heard.t_final) * 1000.0, 1)
            ),
        }
````

### Edit 6: `src/doc_chat/__main__.py`

OLD:

````python
                continue
            spoken = " ".join(self._spoken)
            heard = await self.follow_up(heard, interruption)
````

NEW:

````python
                continue
            spoken = " ".join(self._filler_lines + self._spoken)
            heard = await self.follow_up(heard, interruption)
````

### Edit 7: `src/doc_chat/__main__.py`

OLD:

````python
        self._player = None
        t_final = heard.t_final
````

NEW:

````python
        self._player = None
        self._filler_started = None
        self._filler_lines = []
        self._filler_push = None
        self._loop = asyncio.get_running_loop()
        self._turn_fillers = TurnFillers(self.fillers) if self.fillers is not None else None
        t_final = heard.t_final
````

### Edit 8: `src/doc_chat/__main__.py`

OLD:

````python
        watch = asyncio.create_task(self._watch_for_voice(heard.tail))
        try:
            await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
````

NEW:

````python
        watch = asyncio.create_task(self._watch_for_voice(heard.tail))
        timer = asyncio.create_task(self._filler_timer(t_final))
        try:
            await asyncio.wait({work, watch, timer}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
````

### Edit 9: `src/doc_chat/__main__.py`

OLD:

````python
            watch.cancel()
            raise
        if not watch.done():  # the answer finished without interruption
````

NEW:

````python
            watch.cancel()
            timer.cancel()
            raise
        if timer.done() and not work.done() and not watch.done():  # no answer in time
            watch.cancel()
            await self._give_up(idx, heard, work)
            return None
        timer.cancel()
        if not watch.done():  # the answer finished without interruption
````

### Edit 10: `src/doc_chat/__main__.py`

OLD:

````python
            played = float(level_of()) if level_of is not None else 0.0
            answering = self._answer_started is not None
            guard.ducked = ducker is not None and ducker.ducked
````

NEW:

````python
            played = float(level_of()) if level_of is not None else 0.0
            # a filler is the assistant speaking too: its echo must not count as the user
            answering = self._answer_started is not None or self._filler_started is not None
            guard.ducked = ducker is not None and ducker.ducked
````

### Edit 11: `src/doc_chat/__main__.py`

OLD:

````python
    llm = None
    if args.llm == "qwen":
````

NEW:

````python
    llm = None
    agent_llm = None  # the agent client, for --fillers' tool events
    if args.llm == "qwen":
````

### Edit 12: `src/doc_chat/__main__.py`

OLD:

````python
        llm = AgentChatModel(url=args.agent_url, timeout=args.llm_timeout)
    elif args.llm == "gemini":
````

NEW:

````python
        llm = AgentChatModel(url=args.agent_url, timeout=args.llm_timeout)
        agent_llm = llm
    elif args.llm == "gemini":
````

### Edit 13: `src/doc_chat/__main__.py`

OLD:

````python
                    session.add_prompts.append((line, pcm))
        past = session.history.pairs
````

NEW:

````python
                    session.add_prompts.append((line, pcm))
        if getattr(args, "fillers", False):
            fillers = Fillers.load()

            async def record(line: str) -> bytes:
                pcm, *_ = await client.speak_text(line, voice=args.voice)
                return bytes(pcm)

            print(f"recording {len(fillers.lines())} filler lines...", flush=True)
            await fillers.synthesise(record)  # once, so a filler starts without waiting
            session.fillers = fillers
            if agent_llm is not None:
                agent_llm.on_event = session.on_agent_event
        past = session.history.pairs
````

### Edit 14: `src/doc_chat/__main__.py`

OLD:

````python
    parser.add_argument(
        "--echo-text-guard",
````

NEW:

````python
    parser.add_argument(
        "--fillers",
        action="store_true",
        help="say a short pre-recorded line when an answer is slow: a tool's line when it is "
        "called, a stall at 1.2 s, a notice at 2.5 s, an apology at 8 s (config/fillers.json)",
    )
    parser.add_argument(
        "--echo-text-guard",
````

### Edit 15: `src/doc_chat/agent_client.py`

OLD:

````python
  client -> {"messages": [{"role": "system"|"user"|"assistant", "content": str}, ...]}
  server -> {"text": str} ... then {"done": true}, or {"error": str}
"""
````

NEW:

````python
  client -> {"messages": [{"role": "system"|"user"|"assistant", "content": str}, ...]}
  server -> {"text": str} ... then {"done": true}, or {"error": str}; and
            {"event": "tool", "name": str} when a tool is called (for a filler)
"""
````

### Edit 16: `src/doc_chat/agent_client.py`

OLD:

````python
    timeout: float = 30.0
````

NEW:

````python
    timeout: float = 30.0
    on_event: Any = None  # called with every {"event": ...} message (worker thread)
````

### Edit 17: `src/doc_chat/agent_client.py`

OLD:

````python
                    yield ChatGenerationChunk(message=AIMessageChunk(content=event["text"]))
                elif event.get("done"):
````

NEW:

````python
                    yield ChatGenerationChunk(message=AIMessageChunk(content=event["text"]))
                elif "event" in event:
                    if self.on_event is not None:
                        self.on_event(event)
                elif event.get("done"):
````

## New files, verbatim

Create each file with exactly this content (byte-for-byte, final newline included).

### `src/doc_chat/fillers.py`

````python
"""Fillers: short pre-recorded lines so a slow answer is never silence (R2, .scratch/latency/).

Three moments in a turn, measured from when the transcript is ready (the agent starts then,
~1.0-1.3 s after the user stops): `stall` (no answer yet at `stall_s`, 1.2 s: fast answers get no
filler), `notice` (still nothing at `notice_s`, once, honest) and `giveup` (nothing at
`giveup_s`: the turn is dropped with an apology). A tool call that arrives before anything was
said plays that tool's line instead of a stall. At most one stall-or-tool line per turn; none once
the real answer has started. Each set rotates least-recently-used first, so the same line never
comes twice in a row. Lines never claim a result. The phrases are reviewed by the owner
(`.scratch/latency/fillers-draft.md`) and live in `config/fillers.json`; their audio is
synthesised once at session start, so playing one costs nothing.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG = Path(__file__).resolve().parents[2] / "config" / "fillers.json"


class Fillers:
    """The phrase sets, their rotation and (once synthesised) their audio."""

    def __init__(self, config: dict[str, Any]) -> None:
        timing = config.get("timing", {})
        self.stall_s = float(timing.get("stall_s", 1.2))
        self.notice_s = float(timing.get("notice_s", 2.5))
        self.giveup_s = float(timing.get("giveup_s", 8.0))
        self.sets: dict[str, deque[str]] = {
            "stall": deque(config["stall"]),
            "notice": deque(config["notice"]),
            "giveup": deque(config["giveup"]),
            "default": deque(config["default"]),
            **{f"tool:{name}": deque(lines) for name, lines in config["tools"].items()},
        }
        self.audio: dict[str, bytes] = {}

    @classmethod
    def load(cls, path: Path = CONFIG) -> Fillers:
        return cls(json.loads(path.read_text()))

    def lines(self) -> list[str]:
        """Every distinct line, in a stable order."""
        return sorted({line for lines in self.sets.values() for line in lines})

    async def synthesise(self, speak: Callable[[str], Awaitable[bytes]]) -> None:
        """Record every line once (`speak` returns its 16-bit audio)."""
        for line in self.lines():
            if line not in self.audio:
                self.audio[line] = await speak(line)

    def pick(self, kind: str) -> tuple[str, bytes] | None:
        """The least recently used line of `kind` that has audio, or None; `kind` is
        "stall", "notice", "giveup" or "tool:<name>" (unknown tools use "default")."""
        lines = self.sets.get(kind) or self.sets["default"]
        for _ in range(len(lines)):
            line = lines.popleft()
            lines.append(line)
            if line in self.audio:
                return line, self.audio[line]
        return None


@dataclass
class TurnFillers:
    """What one turn may still play; times are seconds after the transcript was ready."""

    fillers: Fillers
    answered: bool = False  # the real answer has started
    filled: bool = False  # a stall or tool line has played
    noticed: bool = False
    played: list[str] = field(default_factory=list)

    def next_at(self) -> float | None:
        """When the next timed line is due (None once the answer has started)."""
        if self.answered:
            return None
        if not self.filled:
            return self.fillers.stall_s
        if not self.noticed:
            return self.fillers.notice_s
        return self.fillers.giveup_s

    def due(self, elapsed: float) -> str | None:
        """The kind to play now ("stall", "notice", "giveup"), or None; marks it used."""
        at = self.next_at()
        if at is None or elapsed < at:
            return None
        if not self.filled:
            self.filled = True
            return "stall"
        if not self.noticed:
            self.noticed = True
            return "notice"
        self.answered = True  # nothing more after giving up
        return "giveup"

    def tool(self, name: str) -> str | None:
        """The kind to play when a tool call arrives, or None (something was said already)."""
        if self.answered or self.filled:
            return None
        self.filled = True
        return f"tool:{name}"
````

### `config/fillers.json`

````json
{
  "timing": {
    "stall_s": 1.2,
    "notice_s": 2.5,
    "giveup_s": 8.0
  },
  "stall": [
    "Mm, one moment.",
    "Okay, let me see.",
    "Hmm, okay.",
    "Right, just a moment.",
    "Sure, give me a moment.",
    "Let me think.",
    "Got it, one moment."
  ],
  "notice": [
    "Sorry, this is taking a little longer than usual.",
    "Still working on it, the connection's a bit slow.",
    "Bear with me, it's slow on my end right now.",
    "Almost there, sorry for the wait.",
    "Thanks for waiting, I'm still on it.",
    "Sorry about the delay, nearly there.",
    "This one's taking a moment, hang on."
  ],
  "giveup": [
    "Sorry, that's taking too long. Could you ask me again?",
    "I'm having trouble getting an answer right now. Can you try again in a moment?",
    "Sorry, I lost my connection for a second. Could you say that again?",
    "Sorry, I couldn't get that done just now. Want to try once more?",
    "That didn't come through, sorry. Could you ask again?"
  ],
  "tools": {
    "smart_home": [
      "On it.",
      "Okay, doing that now.",
      "Sure, one moment.",
      "Alright, give me a moment.",
      "Okay, executing your request.",
      "Right away.",
      "Sure thing."
    ],
    "get_current_time": [
      "Currently checking the time.",
      "Checking the clock.",
      "Let me look up the time.",
      "Okay, checking the time.",
      "Sure, let me see the time.",
      "Just a moment.",
      "Let me work that out."
    ],
    "convert_time": [
      "Currently checking the time.",
      "Checking the clock.",
      "Let me look up the time.",
      "Okay, checking the time.",
      "Sure, let me see the time.",
      "Just a moment.",
      "Let me work that out."
    ],
    "weather": [
      "Let me check the weather.",
      "One moment, looking at the forecast.",
      "Let me see what the forecast says.",
      "Checking the weather now.",
      "Okay, pulling up the forecast.",
      "Getting the weather now.",
      "Sure, checking the forecast."
    ],
    "wiki_summary": [
      "Let me look that up.",
      "One moment, checking.",
      "Let me see what I can find.",
      "Okay, looking into it.",
      "Good question, let me check.",
      "Let me pull that up.",
      "Just a second, searching."
    ],
    "search_docs": [
      "Let me look that up.",
      "One moment, checking.",
      "Let me see what I can find.",
      "Okay, looking into it.",
      "Good question, let me check.",
      "Let me pull that up.",
      "Just a second, searching."
    ],
    "set_volume": [
      "Sure, adjusting it.",
      "Let me adjust it.",
      "Okay, will adjust it.",
      "One moment, changing the volume.",
      "Sure, setting the volume.",
      "Okay, one second.",
      "Just a moment."
    ],
    "get_volume": [
      "Let me check the speaker.",
      "One moment, asking the Pi.",
      "Okay, checking the speaker.",
      "Let me check on the Pi.",
      "One second, reading that.",
      "Just a moment.",
      "Okay, checking."
    ],
    "device_status": [
      "Let me check the speaker.",
      "One moment, asking the Pi.",
      "Okay, checking the speaker.",
      "Let me check on the Pi.",
      "One second, reading that.",
      "Just a moment.",
      "Okay, checking."
    ]
  },
  "default": [
    "One moment.",
    "Let me check.",
    "Okay, just a second.",
    "Let me see.",
    "Sure, give me a moment.",
    "Looking into it.",
    "Okay, one sec."
  ]
}
````

### `tests/doc_chat/test_fillers.py`

````python
"""fillers: rotation, timing per turn, and the reviewed phrase file."""

import asyncio

from doc_chat.fillers import Fillers, TurnFillers

CONFIG = {
    "timing": {"stall_s": 0.5, "notice_s": 2.5, "giveup_s": 8.0},
    "stall": ["Mm, one moment.", "Okay, let me see.", "Hmm, okay."],
    "notice": ["Sorry, this is taking a little longer than usual."],
    "giveup": ["Sorry, that's taking too long. Could you ask me again?"],
    "default": ["One moment."],
    "tools": {"weather": ["Let me check the weather.", "Checking the weather now."]},
}


def _ready() -> Fillers:
    f = Fillers(CONFIG)
    asyncio.run(f.synthesise(lambda line: _pcm(line)))
    return f


async def _pcm(line: str) -> bytes:
    return line.encode()


def test_lines_rotate_and_never_repeat_back_to_back() -> None:
    f = _ready()
    picked = [f.pick("stall") for _ in range(6)]
    texts = [p[0] for p in picked if p is not None]
    assert texts == ["Mm, one moment.", "Okay, let me see.", "Hmm, okay."] * 2
    assert all(a != b for a, b in zip(texts, texts[1:], strict=False))
    assert picked[0] == ("Mm, one moment.", b"Mm, one moment.")


def test_an_unknown_tool_uses_the_default_set_and_missing_audio_is_skipped() -> None:
    f = _ready()
    assert f.pick("tool:something_new") == ("One moment.", b"One moment.")
    f.audio.pop("Let me check the weather.")
    assert f.pick("tool:weather") == ("Checking the weather now.", b"Checking the weather now.")
    assert Fillers(CONFIG).pick("stall") is None  # nothing synthesised yet: no filler


def test_a_slow_turn_gets_stall_then_notice_then_giveup() -> None:
    turn = TurnFillers(_ready())
    assert turn.next_at() == 0.5 and turn.due(0.4) is None
    assert turn.due(0.5) == "stall" and turn.next_at() == 2.5
    assert turn.due(1.0) is None and turn.due(2.6) == "notice"
    assert turn.due(7.9) is None and turn.due(8.0) == "giveup"
    assert turn.next_at() is None and turn.due(30.0) is None


def test_a_tool_call_before_the_stall_replaces_it() -> None:
    turn = TurnFillers(_ready())
    assert turn.tool("weather") == "tool:weather"
    assert turn.tool("weather") is None  # one per turn
    assert turn.next_at() == 2.5  # the notice is still due if the answer is slow


def test_nothing_plays_once_the_answer_has_started() -> None:
    turn = TurnFillers(_ready())
    turn.answered = True
    assert turn.next_at() is None and turn.due(9.0) is None and turn.tool("weather") is None


def test_the_reviewed_phrase_file_loads() -> None:
    f = Fillers.load()
    assert (f.stall_s, f.notice_s, f.giveup_s) == (1.2, 2.5, 8.0)
    assert len(f.sets["stall"]) == 7 and len(f.sets["giveup"]) == 5
    assert len(f.sets["tool:weather"]) == 7 and len(f.sets["default"]) == 7
    for name in ("smart_home", "get_current_time", "wiki_summary", "set_volume", "device_status"):
        assert len(f.sets[f"tool:{name}"]) == 7, name
````

### `tests/doc_chat/test_fillers_session.py`

````python
"""Fillers in a doc_chat turn: timed lines, the tool line, and giving up (R2)."""

import asyncio
import threading
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from test_bargein import session, turn  # noqa: E402

from doc_chat.__main__ import Session  # noqa: E402
from doc_chat.fillers import Fillers, TurnFillers  # noqa: E402

CONFIG = {
    "timing": {"stall_s": 0.02, "notice_s": 0.05, "giveup_s": 0.1},
    "stall": ["Mm, one moment."],
    "notice": ["Sorry, this is taking a little longer than usual."],
    "giveup": ["Sorry, that's taking too long. Could you ask me again?"],
    "default": ["One moment."],
    "tools": {"weather": ["Let me check the weather."]},
}


def _fillers() -> Fillers:
    f = Fillers(CONFIG)
    f.audio = {line: line.encode() for line in f.lines()}
    return f


def _with_fillers() -> tuple[Session, list[bytes]]:
    s, _client = session()
    s.fillers = _fillers()
    s._turn_fillers = TurnFillers(s.fillers)
    pushed: list[bytes] = []
    s._filler_push = pushed.append
    return s, pushed


async def test_a_slow_turn_plays_the_stall_then_the_notice_then_gives_up() -> None:
    s, pushed = _with_fillers()
    t0 = asyncio.get_running_loop().time()
    import time

    result = await asyncio.wait_for(s._filler_timer(time.perf_counter()), timeout=2)
    assert result == "giveup"
    assert pushed == [b"Mm, one moment.", b"Sorry, this is taking a little longer than usual."]
    assert s._filler_lines == [
        "Mm, one moment.",
        "Sorry, this is taking a little longer than usual.",
    ]
    assert s._filler_started is not None and asyncio.get_running_loop().time() - t0 < 1


async def test_nothing_plays_once_the_answer_has_started() -> None:
    import time

    s, pushed = _with_fillers()
    s._answer_started = time.perf_counter()
    timer = asyncio.create_task(s._filler_timer(time.perf_counter()))
    await asyncio.sleep(0.2)
    assert not timer.done() and pushed == []  # waits quietly until the turn ends
    timer.cancel()


async def test_a_tool_event_from_another_thread_plays_that_tool_s_line() -> None:
    s, pushed = _with_fillers()
    s._loop = asyncio.get_running_loop()
    worker = threading.Thread(target=s.on_agent_event, args=({"event": "tool", "name": "weather"},))
    worker.start()
    worker.join()
    await asyncio.sleep(0.01)
    assert pushed == [b"Let me check the weather."]
    s.on_agent_event({"event": "tool", "name": "weather"})  # one per turn
    await asyncio.sleep(0.01)
    assert pushed == [b"Let me check the weather."]


async def test_giving_up_drops_the_turn_and_logs_it() -> None:
    s, client = session()
    s.fillers = _fillers()
    played: list[str] = []

    async def handle(idx: int, heard: Any) -> None:
        await asyncio.sleep(10)  # the model never answers

    async def play_pcm(text: str, pcm: bytes) -> None:
        played.append(text)

    s.handle = handle  # type: ignore[method-assign]
    s.play_pcm = play_pcm  # type: ignore[method-assign]
    result = await asyncio.wait_for(s.respond(1, turn("will it rain")), timeout=5)
    assert result is None
    assert played == ["Sorry, that's taking too long. Could you ask me again?"]
    assert s.records[-1]["gave_up"] is True
    assert client.tts_cancels == 1
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `uv run --offline --group laptop ruff check src/doc_chat tests/doc_chat` | All checks passed! |
| 2 | `uv run --offline --group laptop ruff format --check src/doc_chat/__main__.py src/doc_chat/agent_client.py src/doc_chat/fillers.py tests/doc_chat/test_fillers.py tests/doc_chat/test_fillers_session.py` | 5 files already formatted |
| 3 | `uv run --offline --group laptop mypy` | Success: no issues found in 56 source files |
| 4 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_fillers.py tests/doc_chat/test_fillers_session.py` | 10 passed |
| 5 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat --deselect tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents` | 253 passed, 3 skipped, 1 deselected |

## Deviations

(none yet)

## Execution Log

