"""The panel's state and the 17 commands of the `smart_home` tool. State is a function of the clock:
there is no background thread (timer, alarm, call and message are checked when read)."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import parse

INTENTS = (
    "PLAY_MUSIC",
    "PAUSE",
    "STOP",
    "NEXT",
    "VOLUME_UP",
    "VOLUME_DOWN",
    "LIGHT_ON",
    "LIGHT_OFF",
    "BRIGHTNESS",
    "COLOR",
    "TEMPERATURE",
    "TIMER",
    "ALARM",
    "CALL",
    "MESSAGE",
    "CREATE_REMINDER",
    "LIST_REMINDERS",
)
MUSIC = {"PLAY_MUSIC", "PAUSE", "STOP", "NEXT", "VOLUME_UP", "VOLUME_DOWN"}
SLOT_FOR_INTENT = {
    "BRIGHTNESS": "PERCENT",
    "COLOR": "COLOR",
    "TEMPERATURE": "DEGREES",
    "TIMER": "DURATION",
    "ALARM": "ALARM_TIME",
    "CREATE_REMINDER": "TASK",
    "CALL": "CONTACT",
    "MESSAGE": "CONTACT",
}
LABELS = {
    "LIGHT_ON": "Lights on",
    "LIGHT_OFF": "Lights off",
    "BRIGHTNESS": "Brightness",
    "COLOR": "Color",
    "TEMPERATURE": "Temperature",
    "TIMER": "Timer",
    "ALARM": "Alarm",
    "CALL": "Call",
    "MESSAGE": "Message",
    "CREATE_REMINDER": "Reminder",
    "LIST_REMINDERS": "Reminders",
}
TEMP_RANGE = (10, 35)
MAX_TIMER_S = 24 * 3600
DONE_SHOWN_S, RING_S, CALL_S, MESSAGE_S, EXPAND_S, STATUS_S = 30, 60, 300, 8, 10, 5
MAX_REMINDERS, MAX_HISTORY, DUPLICATE_S = 50, 10, 60


class CommandError(ValueError):
    """A malformed command: the API answers 400 with this text, which the model hears."""


def load_contacts(path: Path) -> dict[str, str]:
    """{lower-case name: "Name (relation)" parts} from the phonebook JSON."""
    data = json.loads(path.read_text())
    return {c["name"].lower(): c for c in data["contacts"]}


class AppState:
    def __init__(
        self, contacts: dict[str, Any] | None = None, now: Callable[[], datetime] | None = None
    ) -> None:
        self.now = now or datetime.now
        self.contacts = contacts or {}
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.lights: dict[str, Any] = {"on": False, "brightness": 100, "color": "warm white"}
        self.degrees = 24
        self.timer: dict[str, Any] = {"ends": None, "total": 0, "done_at": None}
        self.alarm: dict[str, Any] = {"at": None, "text": None, "rang_at": None}
        self.phone: dict[str, Any] = {"mode": "idle", "contact": None, "since": None}
        self.reminders: list[dict[str, Any]] = []
        self.expanded_until: datetime | None = None
        self.history: list[dict[str, Any]] = []
        self.status: tuple[str, datetime] | None = None

    # --- commands -----------------------------------------------------------------------------

    def command(self, intent: str, slots: dict[str, str]) -> dict[str, Any]:
        """Apply one command; returns the reply body. Raises CommandError for a malformed one."""
        with self.lock:
            when = self.now()
            name = SLOT_FOR_INTENT.get(intent)
            value = str(slots.get(name, "")).strip() if name else ""
            try:
                if intent not in INTENTS:
                    raise CommandError(f"unknown intent {intent!r}")
                if name and not value:
                    raise CommandError(f"{intent} needs the slot {name}")
                changed, message = getattr(self, "_" + intent.lower())(value, when)
            except CommandError as exc:
                self._note(f"{LABELS.get(intent, intent)} {value}".strip(), False, when)
                self.status = (str(exc), when)
                raise
            detected = f"{LABELS[intent]} {value}".strip() if intent in LABELS else intent
            if intent not in MUSIC:
                self._note(detected, True, when)
            return {
                "ok": True,
                "intent": intent,
                "detected": detected,
                "changed": changed,
                "message": message,
                "state": self._snapshot(when),
            }

    def _note(self, text: str, ok: bool, when: datetime) -> None:
        self.history.insert(0, {"time": when.strftime("%H:%M:%S"), "text": text, "ok": ok})
        del self.history[MAX_HISTORY:]

    def _music(self, value: str, when: datetime) -> tuple[bool, str]:
        return False, "music is played by Spotify, not by this panel"

    _play_music = _pause = _stop = _next = _volume_up = _volume_down = _music

    def _light_on(self, value: str, when: datetime) -> tuple[bool, str]:
        changed = not self.lights["on"]
        self.lights["on"] = True
        return changed, "lights on"

    def _light_off(self, value: str, when: datetime) -> tuple[bool, str]:
        changed = self.lights["on"]
        self.lights["on"] = False
        return changed, "lights off"

    def _brightness(self, value: str, when: datetime) -> tuple[bool, str]:
        level = parse.percent(value)
        if level is None or not 0 <= level <= 100:
            raise CommandError(f"brightness must be a percentage from 0 to 100, not {value!r}")
        self.lights.update(on=level > 0, brightness=level or self.lights["brightness"])
        return True, f"brightness {level} percent"

    def _color(self, value: str, when: datetime) -> tuple[bool, str]:
        self.lights.update(color=" ".join(value.lower().split()), on=True)
        return True, f"color {value}"

    def _temperature(self, value: str, when: datetime) -> tuple[bool, str]:
        degrees = parse.degrees(value)
        if degrees is None or not TEMP_RANGE[0] <= degrees <= TEMP_RANGE[1]:
            raise CommandError(
                f"temperature must be {TEMP_RANGE[0]} to {TEMP_RANGE[1]} degrees, not {value!r}"
            )
        changed, self.degrees = degrees != self.degrees, degrees
        return changed, f"thermostat {degrees} degrees"

    def _timer(self, value: str, when: datetime) -> tuple[bool, str]:
        seconds = parse.duration_s(value)
        if not seconds or not 1 <= seconds <= MAX_TIMER_S:
            raise CommandError(f"timer needs a duration from 1 second to 24 hours, not {value!r}")
        self.timer = {"ends": when + timedelta(seconds=seconds), "total": seconds, "done_at": None}
        return True, f"timer {seconds} seconds"

    def _alarm(self, value: str, when: datetime) -> tuple[bool, str]:
        parsed = parse.clock(value)
        if parsed is None:
            raise CommandError(f"alarm needs a clock time like 6:30 AM, not {value!r}")
        at = when.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
        at += timedelta(days=1) if at <= when else timedelta()
        self.alarm = {"at": at, "text": parse.clock_text(*parsed), "rang_at": None}
        return True, f"alarm {self.alarm['text']}"

    def _contact(self, value: str) -> dict[str, Any]:
        found: dict[str, Any] | None = self.contacts.get(value.lower())
        if found is None:
            raise CommandError(f"{value} is not in the phonebook ({self._names()})")
        return found

    def _names(self) -> str:
        return ", ".join(c["name"] for c in self.contacts.values())

    def _call(self, value: str, when: datetime) -> tuple[bool, str]:
        self.phone = {"mode": "calling", "contact": self._contact(value), "since": when}
        return True, f"calling {self.phone['contact']['name']}"

    def _message(self, value: str, when: datetime) -> tuple[bool, str]:
        self.phone = {"mode": "messaging", "contact": self._contact(value), "since": when}
        return True, f"message sent to {self.phone['contact']['name']}"

    def _create_reminder(self, value: str, when: datetime) -> tuple[bool, str]:
        same = self.reminders and self.reminders[0]["task"].lower() == value.lower()
        if same and (when - self.reminders[0]["when"]).total_seconds() < DUPLICATE_S:
            return False, f"reminder already logged: {value}"
        self.reminders.insert(0, {"task": value, "at": when.strftime("%H:%M"), "when": when})
        del self.reminders[MAX_REMINDERS:]
        return True, f"reminder logged: {value}"

    def _list_reminders(self, value: str, when: datetime) -> tuple[bool, str]:
        self.expanded_until = when + timedelta(seconds=EXPAND_S)
        return True, f"{len(self.reminders)} reminders"

    # --- snapshot -----------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return self._snapshot(self.now())

    def _snapshot(self, when: datetime) -> dict[str, Any]:
        lights = {**self.lights, "css": parse.css_color(self.lights["color"])}
        timer = self.timer
        if timer["ends"] is None:
            tstate, remaining = "idle", 0
        elif when >= timer["ends"]:
            timer["done_at"] = timer["done_at"] or timer["ends"]
            if (when - timer["done_at"]).total_seconds() > DONE_SHOWN_S:
                self.timer = {"ends": None, "total": 0, "done_at": None}
                tstate, remaining = "idle", 0
            else:
                tstate, remaining = "done", 0
        else:
            tstate, remaining = "running", math.ceil((timer["ends"] - when).total_seconds())
        h, rest = divmod(remaining, 3600)
        display = f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"
        alarm = self.alarm
        if alarm["at"] is None:
            astate, atext = "idle", None
        elif when >= alarm["at"]:
            alarm["rang_at"] = alarm["rang_at"] or alarm["at"]
            if (when - alarm["rang_at"]).total_seconds() > RING_S:
                self.alarm = {"at": None, "text": None, "rang_at": None}
                astate, atext = "idle", None
            else:
                astate, atext = "ringing", alarm["text"]
        else:
            astate, atext = "set", alarm["text"]
        phone = self.phone
        mode, contact, elapsed = phone["mode"], phone["contact"], 0
        if mode != "idle":
            elapsed = int((when - phone["since"]).total_seconds())
            if elapsed > (CALL_S if mode == "calling" else MESSAGE_S):
                self.phone, mode, contact, elapsed = (
                    {"mode": "idle", "contact": None, "since": None},
                    "idle",
                    None,
                    0,
                )
        if self.status and (when - self.status[1]).total_seconds() > STATUS_S:
            self.status = None
        return {
            "now": when.strftime("%H:%M:%S"),
            "lights": lights,
            "thermostat": {"degrees": self.degrees},
            "timer": {
                "state": tstate,
                "remaining_s": remaining,
                "total_s": timer["total"],
                "display": display,
            },
            "alarm": {"state": astate, "time": atext, "display": atext},
            "phone": {
                "state": mode,
                "contact": contact and contact["name"],
                "relation": contact and contact.get("relation"),
                "elapsed_s": elapsed,
            },
            "reminders": {
                "count": len(self.reminders),
                "expanded": bool(self.expanded_until and when < self.expanded_until),
                "items": [{"task": r["task"], "at": r["at"]} for r in self.reminders],
            },
            "indicator": {
                "detected": next((h["text"] for h in self.history if h["ok"]), ""),
                "status": self.status[0] if self.status else None,
                "history": list(self.history),
            },
        }
