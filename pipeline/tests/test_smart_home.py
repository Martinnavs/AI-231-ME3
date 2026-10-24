from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from voice_pipeline.tools import smart_home


async def _call(arguments: dict[str, Any], base_url: str | None = None) -> tuple[list, list]:
    record: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    async def result_callback(result: dict[str, Any], *, properties: Any = None) -> None:
        results.append(result)

    handler = smart_home.make_handler(base_url=base_url, record=record)
    await handler(SimpleNamespace(arguments=arguments, result_callback=result_callback))
    return record, results


def test_to_command_slotted() -> None:
    assert smart_home.to_command("ALARM", "6 AM") == {
        "intent": "ALARM",
        "slots": {"ALARM_TIME": "6 AM"},
    }


def test_to_command_unslotted_ignores_slot() -> None:
    assert smart_home.to_command("LIGHT_ON", "blue") == {"intent": "LIGHT_ON", "slots": {}}


async def test_handler_records_without_http() -> None:
    record, results = await _call({"intent": "TEMPERATURE", "slot": "22 degrees"})
    assert record == [{"intent": "TEMPERATURE", "slot": "22 degrees"}]
    assert results == [{"ok": True, "message": "recorded"}]


async def test_handler_missing_slot() -> None:
    record, results = await _call({"intent": "TIMER"})
    assert record == [{"intent": "TIMER", "slot": None}]
    assert results[0]["ok"] is False
    assert "45 minutes" in results[0]["message"]


async def test_handler_unknown_intent() -> None:
    _, results = await _call({"intent": "OPEN_DOOR"})
    assert results == [{"ok": False, "message": "unknown intent OPEN_DOOR"}]


async def test_handler_posts_to_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "changed": True, "message": "color -> red"})

    real_client = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(reply), **kwargs)

    monkeypatch.setattr(smart_home.httpx, "AsyncClient", client)
    _, results = await _call({"intent": "COLOR", "slot": "red"}, base_url="http://dash")
    assert seen[0].url.path == "/api/command"
    assert seen[0].read() == b'{"intent":"COLOR","slots":{"COLOR":"red"}}'
    assert results == [{"ok": True, "changed": True, "message": "color -> red"}]


def test_schema_lists_the_phonebook() -> None:
    schema = smart_home.make_schema([{"name": "Anna", "relation": "sister"}])
    assert "Anna (sister)" in schema.description


async def test_handler_rejects_unknown_contact() -> None:
    record, results = await _call({"intent": "CALL", "slot": "Gary"})
    assert record == [{"intent": "CALL", "slot": "Gary"}]
    assert results[0]["ok"] is False
    assert "not in the phonebook" in results[0]["message"]


async def test_handler_accepts_contact_in_any_case() -> None:
    _, results = await _call({"intent": "MESSAGE", "slot": "anna"})
    assert results == [{"ok": True, "message": "recorded"}]
