"""One LLM tool for the 19 ME2 home commands, sent to the ME2 dashboard's POST /api/command."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

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
    "WEATHER",
    "TIME",
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
}

SLOT_VALUES: dict[str, str] = {
    "BRIGHTNESS": "'20 percent', '60 percent', '100 percent'",
    "COLOR": "'white', 'red', 'green', 'blue'",
    "TEMPERATURE": "'18 degrees', '22 degrees', '26 degrees'",
    "TIMER": "'10 seconds', '30 seconds', '1 minute'",
    "ALARM": "'6 AM', '8 AM', '9 PM'",
    "CREATE_REMINDER": "'drink water', 'study', 'exercise'",
}

SCHEMA = FunctionSchema(
    name="smart_home",
    description=(
        "Control or check the home: lights, colour, brightness, music, volume, thermostat, "
        "timer, alarm, reminders, phone call, message, time, weather. One call per command. "
        "Intents that need a slot, with the values the home accepts: "
        + "; ".join(f"{intent}: {values}" for intent, values in SLOT_VALUES.items())
        + ". Every other intent takes no slot."
    ),
    properties={
        "intent": {"type": "string", "enum": list(INTENTS)},
        "slot": {
            "type": "string",
            "description": "The value for BRIGHTNESS, COLOR, TEMPERATURE, TIMER, ALARM or "
            "CREATE_REMINDER, written like the listed values. Omit for other intents.",
        },
    },
    required=["intent"],
)


def to_command(intent: str, slot: str | None) -> dict[str, Any]:
    """The /api/command body; a slot given to an unslotted intent is dropped."""
    name = SLOT_FOR_INTENT.get(intent)
    return {"intent": intent, "slots": {name: slot} if name and slot else {}}


def make_handler(
    *, base_url: str | None, record: list[dict[str, Any]]
) -> Callable[[FunctionCallParams], Awaitable[None]]:
    """Handler that records every call, and posts it to the dashboard when base_url is set."""

    async def handler(params: FunctionCallParams) -> None:
        intent = str(params.arguments.get("intent", ""))
        slot = params.arguments.get("slot")
        record.append({"intent": intent, "slot": slot})
        if intent not in INTENTS:
            result: dict[str, Any] = {"ok": False, "message": f"unknown intent {intent}"}
        elif intent in SLOT_FOR_INTENT and not slot:
            result = {"ok": False, "message": f"{intent} needs a value: {SLOT_VALUES[intent]}"}
        elif base_url is None:
            result = {"ok": True, "message": "recorded"}
        else:
            result = await _post(base_url, to_command(intent, slot))
        await params.result_callback(result)

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
