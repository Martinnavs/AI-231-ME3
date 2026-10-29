"""Local router: a fine-tuned small model (finetune/, served by llama-server) answers simple turns.

    llama-server --jinja -m router-Q4_K_M.gguf --port 8089
    uv run python -m voice_pipeline.agent_server --router-url http://127.0.0.1:8089

For each turn that the regex fast path does not take, the router writes one grammar-constrained
line per call (`LIGHT_ON`, `TIMER|45 minutes`, `weather|Baguio|today`, `none|reply`,
`escalate|why`; the language is finetune/spec/tools.py). A turn is handled locally only when every
line is something this module can run and speak; an `escalate`, a parse failure, an unsupported
name, a timeout or a server that is down all fall through to Gemini, so the router can only make
a turn faster, never lose it.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from voice_pipeline import fast_path

FINETUNE = Path(__file__).resolve().parents[3] / "finetune"
TIMEOUT_S = 1.5  # the router's p95 is about 0.7 s; a slower answer is not worth waiting for
HISTORY_PAIRS = 1  # earlier turns shown to the router: the prompt is re-read from the first change
# (about 100 tokens/s on this CPU), so every extra pair costs about 0.3 s
CHAT_MAX_WORDS = 5  # a `none|` reply is a pleasantry ("thanks" -> "You're welcome."); a 1B model
# answers anything longer with an invention, so longer chat turns go to Gemini
CHAT_REPLY_MAX_WORDS = 8  # likewise a long reply: "Middle aspiration." got an invented definition


ECHO_MIN_WORDS = (
    3  # "Hello." -> "Hello!" is a pleasantry; a longer verbatim repeat is a garbled turn
)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def _echoes(user: str, reply: str) -> bool:
    """The reply just repeats what the user said: the recogniser garbled the turn ("Bucket on Mu.")
    and the model parroted it, which is no answer."""
    said, back = _words(user), _words(reply)
    if len(said.split()) >= ECHO_MIN_WORDS and said in back:
        return True
    return len(back.split()) >= ECHO_MIN_WORDS and back in said


def _load(name: str, path: Path) -> Any:
    """Import a file of the finetune project by path (it is not an installed package)."""
    module_spec = importlib.util.spec_from_file_location(name, path)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module  # dataclasses look their module up here
    module_spec.loader.exec_module(module)
    return module


spec = _load("router_spec_tools", FINETUNE / "spec/tools.py")
_format = _load("router_data_format", FINETUNE / "data/format.py")

# spoken confirmations: the fast path's fixed ones, and one template per intent that takes a slot
NO_SLOT_SAY = {intent: say for intent, say in fast_path.FIXED.values()}
SLOT_SAY = {
    "BRIGHTNESS": "Brightness set to {}.",
    "COLOR": "Color set to {}.",
    "TEMPERATURE": "Temperature set to {}.",
    "TIMER": "Timer set for {}.",
    "ALARM": "Alarm set for {}.",
    "CALL": "Calling {}.",
    "MESSAGE": "Messaging {}.",
    "CREATE_REMINDER": "Reminder set: {}.",
}
INFO_TOOLS = {"weather", "wiki_summary", "get_volume", "set_volume"}  # their result is a sentence


@dataclass
class Routed:
    calls: list[Any] = field(default_factory=list)
    lines: str = ""
    seconds: float = 0.0
    reason: str | None = None  # None: usable; else why this turn goes to Gemini


class LocalRouter:
    def __init__(
        self,
        url: str,
        phonebook: dict[str, str],
        timeout: float = TIMEOUT_S,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.phonebook = phonebook
        self.timeout = timeout
        self.client = client or httpx.AsyncClient(base_url=url)
        self.grammar = spec.grammar()

    async def warm_up(self) -> None:
        """Fill the prompt cache (the first request costs seconds of prefill)."""
        await self.route([], "Say OK.")

    @staticmethod
    def reason_unusable(calls: list[Any], available: set[str], user: str = "") -> str | None:
        """Why these calls cannot be run here (then Gemini takes the turn), or None."""
        if not calls:
            return "empty"
        for call in calls:
            name = call.name
            if name == "escalate":
                return "escalate"
            if name == "none":
                if len(calls) > 1 or not call.args or not call.args[0].strip():
                    return "unsupported: none"
                if len(user.split()) > CHAT_MAX_WORDS:
                    return "chat too long"
                if len(call.args[0].split()) > CHAT_REPLY_MAX_WORDS:
                    return "chat reply too long"
                if _echoes(user, call.args[0]):
                    return "echo"
            elif name in spec.HOME_INTENTS:
                if name == "LIST_REMINDERS" or "smart_home" not in available:
                    return f"unsupported: {name}"
            elif name == "get_current_time":
                try:
                    ZoneInfo(spec.to_tool_args(call).get("timezone", fast_path.TIMEZONE))
                except (ZoneInfoNotFoundError, ValueError):
                    return "unsupported: time zone"
            elif name not in INFO_TOOLS or name not in available:
                return f"unsupported: {name}"
        return None

    async def route(
        self, history: list[tuple[str, str]], user: str, available: set[str] | None = None
    ) -> Routed:
        body = {
            "messages": _format.build_messages(self.phonebook, history, user),
            "grammar": self.grammar,
            "max_tokens": 48,
            "temperature": 0,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        started = time.perf_counter()
        try:
            response = await self.client.post(
                "/v1/chat/completions", json=body, timeout=self.timeout
            )
            response.raise_for_status()
            lines = (response.json()["choices"][0]["message"].get("content") or "").strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return Routed(seconds=time.perf_counter() - started, reason=f"error: {exc!r}")
        seconds = time.perf_counter() - started
        try:
            calls = spec.parse_output(lines)
        except spec.LineError as exc:
            return Routed(lines=lines, seconds=seconds, reason=f"parse: {exc}")
        for (
            call
        ) in calls:  # a home command has one slot: "ALARM|6:30 AM|tomorrow" -> "6:30 AM tomorrow"
            if call.name in spec.HOME_INTENTS and call.args:
                call.args = [" ".join(p.strip() for p in call.args[0].split("|") if p.strip())]
        reason = None if available is None else self.reason_unusable(calls, available, user)
        return Routed(calls, lines, seconds, reason)
