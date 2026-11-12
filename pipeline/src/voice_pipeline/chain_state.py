"""What a multi-step task ("research me, then build my page") is waiting for, kept across turns.

Stored as chain.json next to the delegate's jobs.json; written atomically. An idle chain costs
nothing: `active()` is one comparison. A chain that is not touched for EXPIRE_S, or that three
unrelated turns have passed over, goes back to idle.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PHASES = (
    "idle",
    "awaiting_name",
    "researching",
    "awaiting_fact_approval",
    "building",
    "awaiting_publish_confirm",
)
EXPIRE_S = 600.0
MAX_UNRELATED = 3
MAX_FACTS = 3
MAX_RESEARCH_FACTS = 8  # facts kept while researching, newest last
BLOCK_FACT_CHARS = 700
FACT_CHARS = 160
MAX_ITEMS = 12  # the working set: the things the task is about (cities, phones, events, files)
ITEM_CHARS = 60
MAX_GAPS = 6  # what the deliverable is known to lack (from its manifest)
GAP_CHARS = 80
# tools that belong to a chain; any other tool call while one is active is an unrelated turn
CHAIN_TOOLS = frozenset({"chain_update", "web_research", "web_answer"})


# The user drops the task: whole phrases, any case. Deliberately not "stop that" / "cancel it":
# with music, a timer or the bot's own speech to stop, those do not say which thing is meant, and
# a wrong guess would kill the running job. Stopping the job takes a phrase that names it.
CANCEL = re.compile(
    r"\b(?:never mind|nevermind|forget it|start over"
    r"|(?:stop|cancel|abort|kill|end)\s+(?:the|that|this|my)?\s*"
    r"(?:ongoing|current|running|background)?\s*(?:job|task|build|research))\b",
    re.IGNORECASE,
)
_ANSWER_WORDS = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "no", "nope", "nah", "go"}
    | {"ahead", "do", "it", "that", "thanks", "thank", "you"}
)
_REFERS_BACK = frozenset(
    {"it", "that", "this", "them", "those", "these", "he", "she", "they", "him", "her"}
)
SHORT_TURN_WORDS = 3
# words of a refinement of what was just made ("update it", "add a column", "the other two", "in
# that order"): such a turn belongs to the finished task, it is not a new topic
_REFINES = re.compile(
    r"\b(?:update|change|add|remove|drop|replace|include|also|instead|rather|rest|other|another"
    r"|order|sort|rename|fix|redo|again|table|column|row|section|filled?|missing|unavailable"
    r"|swap|move|merge|combine|shorter|longer|bigger|smaller)\b",
    re.IGNORECASE,
)


def wants_cancel(text: str) -> bool:
    return CANCEL.search(text) is not None


def is_answer(text: str) -> bool:
    """A bare yes, no or okay: it answers the chain's question, it is not another topic."""
    words = re.findall(r"[a-z']+", text.lower())
    return bool(words) and all(w in _ANSWER_WORDS for w in words)


def is_followup(text: str) -> bool:
    """A yes/no answer, a very short turn, or one that points back at what was just made."""
    words = re.findall(r"[a-z']+", text.lower())
    if len(words) <= SHORT_TURN_WORDS or all(w in _ANSWER_WORDS for w in words):
        return True
    return any(w in _REFERS_BACK for w in words) or _REFINES.search(text) is not None


def refines(text: str) -> bool:
    """The turn changes or extends what was just made ("update it", "add a column")."""
    return _REFINES.search(text) is not None


def is_chain_tool(name: str) -> bool:
    return name in CHAIN_TOOLS or name.startswith("delegate")


def _blank() -> dict[str, Any]:
    return {
        "phase": "idle",
        "subject": "",
        "facts": [],
        "items": [],
        "gaps": [],
        "job_id": "",
        "artifact_path": "",
        "unrelated_turns": 0,
        "done": False,
        "focus": {"entity": "", "tool": ""},
        "updated_at": 0.0,
    }


class ChainState:
    def __init__(self, state_dir: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = state_dir / "chain.json"
        self.clock = clock
        self.on_change: Callable[[str], None] | None = None  # "start" / "end" of a chain
        self.data = _blank()
        self.load()

    def load(self) -> None:
        """Read the file (a missing or damaged one is idle); an expired chain resets to idle."""
        try:
            raw = json.loads(self.path.read_text())
            data = {**_blank(), **raw} if isinstance(raw, dict) else _blank()
        except (OSError, ValueError):
            data = _blank()
        if data["phase"] not in PHASES:
            data = _blank()
        self.data = data
        self.active()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.data))
        os.replace(tmp, self.path)

    def update(self, phase: str | None = None, **fields: Any) -> None:
        """Set the phase and any of subject, facts (a list), items (a list that is ADDED to, never
        replaced: the working set), remove (items to drop), gaps (replaced), job_id, artifact_path,
        focus."""
        before = self.data["phase"]
        gone = {str(i).strip().lower() for i in fields.pop("remove", []) or []}
        if phase is not None:
            if phase not in PHASES:
                raise ValueError(f"unknown phase {phase!r}")
            if phase == "idle":
                self.data = _blank()
                self._save()
                self._changed("end")
                return
            if before == "idle":
                self._changed("start")
            self.data["phase"] = phase
        for key, value in fields.items():
            if key not in self.data or key in ("phase", "updated_at", "unrelated_turns"):
                raise ValueError(f"unknown field {key!r}")
            if key == "items":
                merged: dict[str, str] = {}
                for item in [*self.data["items"], *(str(i).strip()[:ITEM_CHARS] for i in value)]:
                    if item and item.lower() not in gone:
                        merged.setdefault(item.lower(), item)
                value = list(merged.values())[:MAX_ITEMS]
            elif key == "gaps":
                value = [str(g).strip()[:GAP_CHARS] for g in value if str(g).strip()][:MAX_GAPS]
            if key == "facts":
                new = [str(f).strip()[:FACT_CHARS] for f in value if str(f).strip()]
                if "researching" in (before, self.data["phase"]):  # research adds up across calls
                    seen: dict[str, str] = {}
                    for fact in [*self.data["facts"], *new]:
                        seen.pop(fact.lower(), None)
                        seen[fact.lower()] = fact
                    value = list(seen.values())[-MAX_RESEARCH_FACTS:]
                else:
                    value = new[:MAX_FACTS]
            self.data[key] = value
        if gone and "items" not in fields:
            self.data["items"] = [i for i in self.data["items"] if i.lower() not in gone]
        self.data["unrelated_turns"] = 0
        self.data["done"] = False
        self.data["updated_at"] = self.clock()
        self._save()

    def mark_done(self) -> None:
        """The chain's deliverable is finished; the next turn that is not a follow-up ends it."""
        self.data["done"] = True
        self._save()

    def touch_unrelated(self) -> None:
        if self.data["phase"] != "idle":
            self.data["unrelated_turns"] += 1
            self._save()

    def active(self) -> bool:
        if self.data["phase"] == "idle":
            return False
        if (
            self.clock() - self.data["updated_at"] > EXPIRE_S
            or self.data["unrelated_turns"] >= MAX_UNRELATED
        ):
            self.data = _blank()
            self._save()
            self._changed("end")
            return False
        return True

    def _changed(self, event: str) -> None:
        if self.on_change is not None:
            self.on_change(event)

    def block(self) -> str:
        """One to three lines for the end of the user's message, or "" when idle."""
        if not self.active():
            return ""
        d = self.data
        parts = [f"phase={d['phase']}"]
        if d["subject"]:
            parts.append(f'subject="{d["subject"]}"')
        if d["facts"]:
            facts = " ; ".join(d["facts"][-MAX_RESEARCH_FACTS:])
            if len(facts) > BLOCK_FACT_CHARS:
                facts = facts[: BLOCK_FACT_CHARS - 1] + "…"
            parts.append(f'facts="{facts}"')
        if d["items"]:
            parts.append(f'items="{" ; ".join(d["items"])}"')
        if d["job_id"]:
            parts.append(f"job={d['job_id']}")
        lines = ["[ACTIVE TASK: " + " | ".join(parts) + "]"]
        if d["artifact_path"]:
            missing = f" | missing: {' ; '.join(d['gaps'])}" if d["gaps"] else ""
            lines.append(f"[ARTIFACT: {d['artifact_path']}{missing}]")
        return "\n".join(lines)
