"""Say the Pi's `device_status` result as a sentence, answering what was asked.

The tool returns numbers (CPU temperature, uptime, load, free memory, whether the speaker is
connected). A router turn has no model to word them, so this picks the parts the question is
about, or all of them when it names none.
"""

from __future__ import annotations

import json
import re
from typing import Any

TOPICS = {
    "temperature": re.compile(r"\b(temperature|temp|hot|warm|heat|cool|degrees)\b"),
    "memory": re.compile(r"\b(memory|ram)\b"),
    "uptime": re.compile(r"\b(uptime|up ?time|running|how long)\b"),
    "speaker": re.compile(r"\b(connect(ed)?|plugged|speaker)\b"),
}


# a status question joined to a request of another kind ("how hot is the Pi and what is the
# weather") needs the model: only it can make both calls and word one answer
_JOINED = re.compile(r"\b(and|then|also|plus|but|while)\b")
_OTHER_REQUEST = re.compile(
    r"\b(weather|rain|forecast|time|date|lights?|music|songs?|play|timer|alarm|remind\w*|call|"
    r"text|message|volume|louder|quieter|turn|set|open|close|search|news)\b"
)


def single_question(question: str) -> bool:
    """The question is about the Pi alone, so the status sentence answers all of it."""
    asked = question.lower()
    joined_to_other = _JOINED.search(asked) and _OTHER_REQUEST.search(asked)
    return not joined_to_other and asked.count("?") < 2


def parse(result: Any) -> dict[str, Any] | None:
    """The status dict: given as one, or as the JSON text the MCP client hands back."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        start = result.find("{")
        try:
            value = json.loads(result[start:]) if start >= 0 else None
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


def duration(minutes: float) -> str:
    minutes = round(minutes)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(minutes, 60)
    text = f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{text} {rest} minute{'s' if rest != 1 else ''}" if rest else text


def amount(megabytes: float) -> str:
    return (
        f"{megabytes / 1024:.1f} gigabytes" if megabytes >= 1024 else f"{megabytes:.0f} megabytes"
    )


def device_sentence(result: Any, question: str) -> str | None:
    """One spoken sentence for `question`, or None when the result holds nothing usable."""
    data = parse(result)
    if not data:
        return None
    asked = question.lower()
    wanted = {name for name, pattern in TOPICS.items() if pattern.search(asked)} or set(TOPICS)
    parts: list[str] = []
    temp, memory, uptime = (
        data.get("cpu_temperature_c"),
        data.get("free_memory_mb"),
        data.get("uptime_minutes"),
    )
    if "temperature" in wanted and isinstance(temp, int | float):
        parts.append(f"the Pi is at {temp:.0f} degrees Celsius")
    if "uptime" in wanted and isinstance(uptime, int | float):
        parts.append(f"it has been up for {duration(uptime)}")
    if "memory" in wanted and isinstance(memory, int | float):
        parts.append(f"it has {amount(memory)} of memory free")
    if "speaker" in wanted and isinstance(data.get("speaker_connected"), bool):
        parts.append(
            "the speaker is connected"
            if data["speaker_connected"]
            else "the speaker is not connected"
        )
    if not parts:
        return None
    text = (
        ", and ".join(parts)
        if len(parts) == 2
        else ", ".join(parts[:-1]) + f", and {parts[-1]}"
        if len(parts) > 2
        else parts[0]
    )
    return text[0].upper() + text[1:] + "."
