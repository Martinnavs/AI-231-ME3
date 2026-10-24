# Ticket 01: fast path: well-known commands skip the LLM (R3)

**Status:** ready
**Date:** 2026-10-24
**Blocked by:** None

Common, unambiguous commands (lights, music, volume up/down, timers, alarms, the speaker volume with a number) are matched by plain rules on the last user message and run the real tool directly, with a fixed spoken result, so they never wait for the LLM (two requests per tool turn, spikes of 0.7-7.7 s seen in the spoken check). Anything not matched goes to the LLM exactly as before. Checked by the assistant on a scratch copy: 11 of the 82 single-turn dry-run prompts match, all with the expected tool and arguments.

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `pipeline/src/voice_pipeline/agent_server.py` | edit (list below) | **152** (now 106) |
| `pipeline/tests/test_agent_server.py` | edit (list below) | **146** (now 77) |
| `pipeline/src/voice_pipeline/fast_path.py` | new, verbatim | **157** |
| `pipeline/tests/test_fast_path.py` | new, verbatim | **106** |

**Non-goals / touch nothing else:** no change to the root project, `agent.py`, `agent_tools.py`, the prompts or either project's `pyproject.toml`/`uv.lock`; no fillers (tickets 02-03); never start the agent server or run the dry run (paid API).

**Security-sensitive:** no secrets; the fast path calls the same tool handlers the LLM would, with arguments from fixed rules.

## Edits

Apply in this order. Each OLD block occurs exactly once in the file as it is at that moment; replace it with NEW, byte-for-byte.

### Edit 1: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
from pathlib import Path
from typing import Any
````

NEW:

````python
from pathlib import Path
from types import SimpleNamespace
from typing import Any
````

### Edit 2: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python

from voice_pipeline.agent import Agent, build_llm
````

NEW:

````python

from voice_pipeline import fast_path
from voice_pipeline.agent import Agent, build_llm
````

### Edit 3: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python

    def __init__(self, agent: Any, record: list[dict[str, Any]], log: Path) -> None:
        self.agent = agent
````

NEW:

````python

    def __init__(
        self, agent: Any, record: list[dict[str, Any]], log: Path, tools: Any = None
    ) -> None:
        self.agent = agent
````

### Edit 4: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
        self.lock = asyncio.Lock()
````

NEW:

````python
        self.lock = asyncio.Lock()
        # with the agent's tools, common home commands skip the LLM (fast_path.py)
        self.tools = {t.name: t for t in tools.standard_tools} if tools is not None else {}

    async def fast(self, found: fast_path.Match, on_text: Any) -> dict[str, Any]:
        """Run the tool the LLM would have called, then say the fixed confirmation."""
        started = time.perf_counter()
        results: list[Any] = []

        async def done(result: Any, *args: Any, **kwargs: Any) -> None:
            results.append(result)

        params = SimpleNamespace(
            function_name=found.tool,
            tool_call_id="fast-path",
            arguments=dict(found.args),
            result_callback=done,
        )
        await self.tools[found.tool].handler(params)
        text = fast_path.spoken_result(found, results[0] if results else {"ok": False})
        await on_text(text)
        elapsed = time.perf_counter() - started
        return {"status": "ok", "text": text, "first_call_s": 0.0, "first_text_s": elapsed,
                "end_s": elapsed, "fast_path": True}  # fmt: skip
````

### Edit 5: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
            before = len(self.record)
            turn = asyncio.create_task(self.agent.ask(messages, reset=True, on_text=on_text))
            closed = asyncio.create_task(ws.wait_closed())
````

NEW:

````python
            before = len(self.record)
            last = messages[-1] if messages else {}
            found = (
                fast_path.match(str(last.get("content", "")))
                if last.get("role") == "user"
                else None
            )
            if found is not None and found.tool in self.tools:
                turn = asyncio.create_task(self.fast(found, on_text))
            else:
                turn = asyncio.create_task(self.agent.ask(messages, reset=True, on_text=on_text))
            closed = asyncio.create_task(ws.wait_closed())
````

### Edit 6: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python

async def run(port: int, live: str | None, system_file: Path, pi_url: str | None) -> int:
    record: list[dict[str, Any]] = []
````

NEW:

````python

async def run(
    port: int, live: str | None, system_file: Path, pi_url: str | None, fast: bool = True
) -> int:
    record: list[dict[str, Any]] = []
````

### Edit 7: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
    log.parent.mkdir(parents=True, exist_ok=True)
    server = AgentServer(agent, record, log)
    async with serve(server.handle, "127.0.0.1", port):
````

NEW:

````python
    log.parent.mkdir(parents=True, exist_ok=True)
    server = AgentServer(agent, record, log, tools if fast else None)
    async with serve(server.handle, "127.0.0.1", port):
````

### Edit 8: `pipeline/src/voice_pipeline/agent_server.py`

OLD:

````python
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.port, args.live, args.system, args.pi_url or None))
    except KeyboardInterrupt:
````

NEW:

````python
    parser.add_argument("--pi-url", default=PI_URL, help="the Pi's device tools; '' for none")
    parser.add_argument(
        "--fast-path",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="answer common home commands without the LLM (fast_path.py)",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(
            run(args.port, args.live, args.system, args.pi_url or None, args.fast_path)
        )
    except KeyboardInterrupt:
````

### Edit 9: `pipeline/tests/test_agent_server.py`

OLD:

````python
    assert json.loads((tmp_path / "log.jsonl").read_text())["cancelled"] is True
````

NEW:

````python
    assert json.loads((tmp_path / "log.jsonl").read_text())["cancelled"] is True


class FakeTool:
    """A tool handler like the agent's: records the call and returns a result."""

    def __init__(self, name: str, record: list[dict[str, Any]], result: Any) -> None:
        self.name = name
        self.record = record
        self.result = result

    async def handler(self, params: Any) -> None:
        self.record.append({"tool": self.name, "args": dict(params.arguments)})
        await params.result_callback(self.result)


async def _serve_fast(
    agent: FakeAgent, record: list[dict[str, Any]], log: Path, result: Any
) -> tuple[Any, str]:
    tools = type("Tools", (), {})()
    tools.standard_tools = [FakeTool("smart_home", record, result)]
    server = AgentServer(agent, record, log, tools)
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    port = next(iter(ws_server.sockets)).getsockname()[1]
    return ws_server, f"ws://127.0.0.1:{port}"


async def test_a_common_command_skips_the_llm(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = FakeAgent(record)
    ws_server, url = await _serve_fast(agent, record, tmp_path / "log.jsonl", {"ok": True})
    async with connect(url) as ws:
        await ws.send(
            json.dumps({"messages": [{"role": "user", "content": "Set a timer for ten minutes."}]})
        )
        events = [json.loads(await ws.recv()) for _ in range(2)]
    ws_server.close()
    assert events == [{"text": "Timer set for 10 minutes."}, {"done": True}]
    assert agent.asked == []  # the LLM was not called
    row = json.loads((tmp_path / "log.jsonl").read_text())
    assert row["fast_path"] is True
    assert row["calls"] == [
        {"tool": "smart_home", "args": {"intent": "TIMER", "slot": "10 minutes"}}
    ]


async def test_a_failed_command_says_why(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    failed = {"ok": False, "message": "home dashboard unreachable"}
    ws_server, url = await _serve_fast(FakeAgent(record), record, tmp_path / "log.jsonl", failed)
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "Lights off."}]}))
        first = json.loads(await ws.recv())
    ws_server.close()
    assert first == {"text": "Sorry, that didn't work: home dashboard unreachable."}


async def test_anything_else_still_goes_to_the_llm(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = FakeAgent(record)
    ws_server, url = await _serve_fast(agent, record, tmp_path / "log.jsonl", {"ok": True})
    async with connect(url) as ws:
        await ws.send(
            json.dumps(
                {"messages": [{"role": "user", "content": "Turn on the lights and play music."}]}
            )
        )
        await ws.recv()
    ws_server.close()
    assert agent.asked == [[{"role": "user", "content": "Turn on the lights and play music."}]]
````

## New files, verbatim

Create each file with exactly this content (byte-for-byte, final newline included).

### `pipeline/src/voice_pipeline/fast_path.py`

````python
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
````

### `pipeline/tests/test_fast_path.py`

````python
"""fast_path: exact phrasings of common home commands, answered without the LLM."""

import json
from pathlib import Path

import pytest

from voice_pipeline.fast_path import Match, clock, match, number, spoken_result

PROMPTS = Path(__file__).resolve().parents[1] / "config" / "eval" / "agent_prompts.jsonl"


@pytest.mark.parametrize(
    ("said", "tool", "args", "say"),
    [
        ("Turn on the lights.", "smart_home", {"intent": "LIGHT_ON"}, "The lights are on."),
        ("Please turn the lights off, thanks", "smart_home", {"intent": "LIGHT_OFF"},
         "The lights are off."),
        ("Kill the lights!", "smart_home", {"intent": "LIGHT_OFF"}, "The lights are off."),
        ("Play some music.", "smart_home", {"intent": "PLAY_MUSIC"}, "Playing music."),
        ("Pause the music.", "smart_home", {"intent": "PAUSE"}, "Paused."),
        ("Stop the music.", "smart_home", {"intent": "STOP"}, "Music stopped."),
        ("Skip this song", "smart_home", {"intent": "NEXT"}, "Skipping to the next one."),
        ("Turn it up.", "smart_home", {"intent": "VOLUME_UP"}, "Turning it up."),
        ("Lower the volume.", "smart_home", {"intent": "VOLUME_DOWN"}, "Turning it down."),
        ("Set a timer for ten minutes.", "smart_home",
         {"intent": "TIMER", "slot": "10 minutes"}, "Timer set for 10 minutes."),
        ("Timer for forty five seconds", "smart_home",
         {"intent": "TIMER", "slot": "45 seconds"}, "Timer set for 45 seconds."),
        ("Set a timer for 1 hour.", "smart_home", {"intent": "TIMER", "slot": "1 hour"},
         "Timer set for 1 hour."),
        ("Set an alarm for 6:30 AM.", "smart_home", {"intent": "ALARM", "slot": "6:30 AM"},
         "Alarm set for 6:30 AM."),
        ("Wake me up at seven pm", "smart_home", {"intent": "ALARM", "slot": "7:00 PM"},
         "Alarm set for 7:00 PM."),
        ("Set the speaker volume to 40 percent.", "set_volume", {"percent": 40}, None),
        ("Set the volume to twenty percent", "set_volume", {"percent": 20}, None),
    ],
)  # fmt: skip
def test_the_listed_phrasings_match(
    said: str, tool: str, args: dict[str, object], say: str | None
) -> None:
    assert match(said) == Match(tool, args, say)


@pytest.mark.parametrize(
    "said",
    [
        "Turn on the lights and play some music.",  # two commands: the LLM
        "Can you turn on the lights?",  # a question
        "Turn on the kitchen lights.",  # not an exact phrasing
        "Make it warmer.",
        "Set the lights to purple.",
        "Set a timer.",  # no value
        "Set a timer for a bit.",
        "Set an alarm for later.",
        "Set the volume to 150 percent.",  # out of range
        "Stop.",  # a stop word: doc_chat's job
        "What time is it?",
        "",
    ],
)
def test_everything_else_goes_to_the_llm(said: str) -> None:
    assert match(said) is None


def test_numbers_and_clock_times() -> None:
    assert [number(w) for w in ("7", "ten", "forty five", "ninety", "a hundred")] == [
        7, 10, 45, 90, 100,
    ]  # fmt: skip
    assert number("a bit") is None and number("twenty twenty") is None
    assert clock("6:30 am") == "6:30 AM" and clock("seven pm") == "7:00 PM"
    assert clock("six thirty am") == "6:30 AM"
    assert clock("13 pm") is None and clock("later") is None


def test_what_is_said_after_the_tool() -> None:
    lights = Match("smart_home", {"intent": "LIGHT_ON"}, "The lights are on.")
    assert spoken_result(lights, {"ok": True, "message": "recorded"}) == "The lights are on."
    assert spoken_result(lights, {"ok": False, "message": "home dashboard unreachable"}) == (
        "Sorry, that didn't work: home dashboard unreachable."
    )
    volume = Match("set_volume", {"percent": 40}, None)
    assert spoken_result(volume, "The speaker volume is now 40 percent.") == (
        "The speaker volume is now 40 percent."
    )


def test_on_the_dry_run_prompts_every_match_is_the_expected_call() -> None:
    matched = 0
    for line in PROMPTS.read_text().splitlines():
        prompt = json.loads(line)
        if len(prompt["turns"]) != 1:
            continue
        found = match(prompt["turns"][0])
        if found is None:
            continue
        matched += 1
        expect = prompt["expect"][0]
        assert len(expect) == 1, prompt["id"]
        if found.tool == "smart_home":
            assert expect[0].get("intent") == found.args["intent"], prompt["id"]
            assert (expect[0].get("slot") or None) == found.args.get("slot"), prompt["id"]
        else:
            assert expect[0].get("tool") == found.tool, prompt["id"]
    assert matched >= 8  # the canonical commands are covered
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `cd pipeline && uv run --offline ruff check src tests` | All checks passed! |
| 2 | `cd pipeline && uv run --offline ruff format --check src tests` | 14 files already formatted |
| 3 | `cd pipeline && uv run --offline mypy` | Success: no issues found in 9 source files |
| 4 | `cd pipeline && uv run --offline pytest -q -p no:cacheprovider tests/test_fast_path.py tests/test_agent_server.py` | 36 passed |
| 5 | `cd pipeline && uv run --offline pytest -q -p no:cacheprovider` | 58 passed |

## Deviations

(none yet)

## Execution Log

