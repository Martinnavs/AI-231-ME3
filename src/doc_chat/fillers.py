"""Fillers: short pre-recorded lines so a slow answer is never silence (R2, .scratch/latency/).

Three moments in a turn, measured from when the transcript is ready (the agent starts then,
~1.0-1.3 s after the user stops): `stall` (no answer yet at `stall_s`, 1.2 s: fast answers get no
filler), `notice` (still nothing at `notice_s`, once, honest) and `giveup` (nothing at
`giveup_s`: the turn is dropped with an apology). A timed line never starts until the previous
filler has finished playing and `gap_s` of silence has passed, so lines never run together.
A tool call that arrives before anything was said plays that tool's line instead of a stall. At most one stall-or-tool line per turn; none once
the real answer has started. Each set rotates least-recently-used first, so the same line never
comes twice in a row. Lines never claim a result. The phrases are reviewed by the owner
(`.scratch/latency/fillers-draft.md`) and live in `config/fillers.json`; their audio is
synthesised once at session start, so playing one costs nothing.
"""

from __future__ import annotations

import json
import re
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
        self.gap_s = float(timing.get("gap_s", 0.0))  # silence left after a filler finishes playing
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

    def tool(self, name: str, said: bool = False) -> str | None:
        """The kind to play when a tool call arrives, or None (something was said already, or the
        model's own sentence for the call is on its way: `said`)."""
        if self.answered or self.filled:
            return None
        self.filled = True
        return None if said else f"tool:{name}"

    def text_arrived(self) -> None:
        """The agent's answer text has started: its audio is imminent, so no stall line."""
        self.filled = True


def strip_fillers(text: str, lines: list[str]) -> str:
    """`text` without any filler line in it: a pause let one play, the recogniser then heard it
    as part of the user's next words ("...Nobel Prizes? Almost there. Got it.")."""
    for line in sorted(lines, key=len, reverse=True):
        pattern = r"\s*".join(re.escape(w) for w in line.split())
        text = re.sub(rf"(?i)(?<!\w){pattern}", " ", text)
    return " ".join(text.split())
