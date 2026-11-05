"""One LLM tool for 17 ME2 home commands, sent to the ME2 dashboard's POST /api/command.

Time and weather are not home commands any more: the agent answers them with the real
time and weather MCP tools (owner, 2026-10-24).

Slot values are open (any time, duration, temperature, colour, brightness, reminder text), except
CALL and MESSAGE, whose slot must be a contact in the JSON phonebook.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams

PHONEBOOK = Path(__file__).resolve().parents[3] / "config" / "phonebook.json"

INTENTS: tuple[str, ...] = (
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

SLOT_FOR_INTENT: dict[str, str] = {
    "BRIGHTNESS": "PERCENT",
    "COLOR": "COLOR",
    "TEMPERATURE": "DEGREES",
    "TIMER": "DURATION",
    "ALARM": "ALARM_TIME",
    "CREATE_REMINDER": "TASK",
    "CALL": "CONTACT",
    "MESSAGE": "CONTACT",
}

# An optional slot: PLAY_MUSIC may say what to play. The router's line protocol has none (it keeps
# bare music only, call_policy.bare_music); the model fills it for "play Taylor Swift".
OPTIONAL_SLOT_FOR_INTENT: dict[str, str] = {"PLAY_MUSIC": "QUERY"}

SLOT_HINTS: dict[str, str] = {
    "BRIGHTNESS": "a percentage, like '40 percent'",
    "COLOR": "any colour, like 'purple' or 'warm white'",
    "TEMPERATURE": "degrees, like '24 degrees'",
    "TIMER": "a duration, like '45 minutes' or '1 hour 30 minutes'",
    "ALARM": "a clock time, like '6:30 AM' or '7:15 PM'",
    "CREATE_REMINDER": "what to be reminded of, in the user's words",
    "CALL": "a contact name from the phonebook",
    "MESSAGE": "a contact name from the phonebook",
}


def load_phonebook(path: Path = PHONEBOOK) -> list[dict[str, str]]:
    """The contacts: [{"name": ..., "relation": ...}, ...]."""
    contacts: list[dict[str, str]] = json.loads(path.read_text())["contacts"]
    return contacts


def make_schema(contacts: list[dict[str, str]]) -> FunctionSchema:
    """The smart_home function schema; the phonebook names go into its description."""
    phonebook = ", ".join(f"{c['name']} ({c['relation']})" for c in contacts)
    return FunctionSchema(
        name="smart_home",
        description=(
            "Control or check the home: lights, colour, brightness, music, volume, thermostat, "
            "timer, alarm, reminders, phone call, message. One call per command. "
            "Intents that need a slot, and its form: "
            + "; ".join(f"{intent}: {hint}" for intent, hint in SLOT_HINTS.items())
            + ". Optional: PLAY_MUSIC: what to play (an artist, song, genre or playlist), spelled "
            "as it should be if the transcript garbled it, and omitted for plain music"
            + f". Phonebook: {phonebook}. Every other intent takes no slot."
        ),
        properties={
            "intent": {"type": "string", "enum": list(INTENTS)},
            "slot": {
                "type": "string",
                "description": "The value for BRIGHTNESS, COLOR, TEMPERATURE, TIMER, ALARM, "
                "CREATE_REMINDER, CALL or MESSAGE, in the form listed; for PLAY_MUSIC what to "
                "play, if the user named something. Omit for other intents.",
            },
        },
        required=["intent"],
    )


SCHEMA = make_schema(load_phonebook())


def to_command(intent: str, slot: str | None) -> dict[str, Any]:
    """The /api/command body; a slot given to an unslotted intent is dropped."""
    name = SLOT_FOR_INTENT.get(intent) or OPTIONAL_SLOT_FOR_INTENT.get(intent)
    return {"intent": intent, "slots": {name: slot} if name and slot else {}}


def make_handler(
    *,
    base_url: str | None,
    record: list[dict[str, Any]],
    contacts: list[dict[str, str]] | None = None,
) -> Callable[[FunctionCallParams], Awaitable[None]]:
    """Handler that records every call, and posts it to the dashboard when base_url is set."""
    names = {c["name"].lower(): c["name"] for c in contacts or load_phonebook()}

    async def handler(params: FunctionCallParams) -> None:
        intent = str(params.arguments.get("intent", ""))
        slot = params.arguments.get("slot")
        record.append({"intent": intent, "slot": slot})
        if intent not in INTENTS:
            result: dict[str, Any] = {"ok": False, "message": f"unknown intent {intent}"}
        elif intent in SLOT_FOR_INTENT and not slot:
            result = {"ok": False, "message": f"{intent} needs a value: {SLOT_HINTS[intent]}"}
        elif intent in ("CALL", "MESSAGE") and str(slot).lower() not in names:
            known = ", ".join(names.values())
            result = {"ok": False, "message": f"{slot} is not in the phonebook ({known})"}
        elif base_url is None:
            result = {"ok": True, "message": "recorded"}
            if intent in OPTIONAL_SLOT_FOR_INTENT and slot:
                result["message"] = f"Playing {str(slot).strip().rstrip('.')}."
        else:
            result = await _post(base_url, to_command(intent, slot))
            if result.get("ok") and intent in OPTIONAL_SLOT_FOR_INTENT and slot:
                result["message"] = f"Playing {str(slot).strip().rstrip('.')}."
        # a command that worked needs no second LLM request: the model's `say` is the reply
        # (TurnTap checks that there is one)
        await params.result_callback(
            result,
            properties=FunctionCallResultProperties(run_llm=False) if result.get("ok") else None,
        )

    return handler


async def _post(base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=2.0) as client:
            response = await client.post("/api/command", json=body)
    except httpx.HTTPError:
        return {"ok": False, "message": "home dashboard unreachable"}
    data = response.json()
    if response.status_code != 200:
        return {"ok": False, "message": str(data.get("error", response.status_code))}
    return {"ok": True, "changed": data.get("changed"), "message": data.get("message")}
