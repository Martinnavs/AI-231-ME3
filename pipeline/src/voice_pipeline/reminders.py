"""Reminders kept on the machine that runs the agent, so "list my reminders" has something to read.

Without the ME2 dashboard (`--live`) the smart_home tool only recorded a reminder and the agent said
"Reminder set". This store keeps them in one small JSON file (`REMINDERS_FILE`, default
~/.local/state/voice_agent/reminders.json), newest last, and `spoken_latest` words the newest three
for the listener: the count, then the reminders one after another, latest first.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

DEFAULT_PATH = Path(
    os.environ.get("REMINDERS_FILE", Path.home() / ".local/state/voice_agent/reminders.json")
)
MAX_KEPT = 50
DUPLICATE_S = 60.0  # the same text twice within this many seconds is one reminder (router + model)
NUMBER_WORDS = {1: "one", 2: "two", 3: "three"}


class Reminders:
    def __init__(self, path: Path = DEFAULT_PATH, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock

    def _read(self) -> list[dict[str, object]]:
        try:
            rows = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        return [r for r in rows if isinstance(r, dict) and isinstance(r.get("text"), str)]

    def add(self, text: str) -> bool:
        """Keep a reminder; False when it only repeats the newest one (a double call)."""
        text = " ".join(text.split()).strip(" .")
        if not text:
            return False
        rows = self._read()
        now = self.clock()
        if (
            rows
            and rows[-1]["text"] == text
            and now - float(str(rows[-1].get("at", 0))) < DUPLICATE_S
        ):
            return False
        rows.append({"text": text, "at": now})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-MAX_KEPT:]))
        with contextlib.suppress(OSError):
            tmp.replace(self.path)
        return True

    def latest(self, n: int = 3) -> list[str]:
        """The newest `n` reminders, newest first."""
        return [str(r["text"]) for r in reversed(self._read())][:n]

    def count(self) -> int:
        return len(self._read())

    def spoken_latest(self, n: int = 3) -> str:
        """The count, then the newest `n` reminders, newest first, as one sentence to say."""
        total = self.count()
        if total == 0:
            return "You have no reminders yet."
        items = self.latest(n)
        if total == 1:
            return f"You have one reminder: {items[0]}."
        if total <= n:
            word = NUMBER_WORDS.get(total, str(total))
            return f"You have {word} reminders, newest first: " + "; ".join(items) + "."
        word = NUMBER_WORDS.get(n, str(n))
        return (
            f"You have {total} reminders. The latest {word}, newest first: "
            + "; ".join(items)
            + "."
        )
