"""device_status is said from the tool result (no second model request) unless more was asked."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema

from voice_pipeline import question
from voice_pipeline.agent_tools import PI_URL_FALLBACK, _recorded, pi_url_from_env
from voice_pipeline.device_say import single_question

STATUS = json.dumps(
    {
        "cpu_temperature_c": 54.5,
        "uptime_minutes": 135,
        "free_memory_mb": 1730,
        "speaker_connected": True,
    }
)


async def deliver(asked: str, result: Any = STATUS, call_id: str = "call-1") -> tuple[Any, Any]:
    """(what reaches the pipeline, the properties) when device_status returns `result`."""
    seen: list[tuple[Any, Any]] = []

    async def handler(params: Any) -> None:
        await params.result_callback(result)

    async def result_callback(value: Any, *, properties: Any = None) -> None:
        seen.append((value, properties))

    schema = FunctionSchema(
        name="device_status", description="d", properties={}, required=[], handler=handler
    )
    question.current = asked
    try:
        wrapped = _recorded(schema, [])
        assert wrapped.handler is not None
        params = SimpleNamespace(
            arguments={}, result_callback=result_callback, tool_call_id=call_id
        )
        await wrapped.handler(params)  # type: ignore[arg-type]
    finally:
        question.current = ""
    return seen[0]


@pytest.mark.parametrize(
    ("asked", "sentence"),
    [
        ("How hot is the Pi?", "The Pi is at 54 degrees Celsius."),
        ("How much memory does the Pi have free?", "It has 1.7 gigabytes of memory free."),
        ("How long has the Pi been running?", "It has been up for 2 hours 15 minutes."),
        ("Is the speaker connected?", "The speaker is connected."),
        (
            "What's the temperature and is the speaker plugged in?",
            "degrees Celsius, and the speaker",
        ),
        ("How is the Pi doing?", "gigabytes of memory free"),
    ],
)
async def test_the_status_is_said_without_a_second_model_request(asked: str, sentence: str) -> None:
    value, properties = await deliver(asked)
    assert isinstance(value, str) and sentence.lower() in value.lower()
    assert properties is not None and properties.run_llm is False


@pytest.mark.parametrize(
    "asked",
    [
        "How hot is the Pi and what's the weather like in Davao?",
        "Is the speaker connected, and then play some music",
        "How much memory is free? And turn the volume up.",
        "How hot is the Pi? What time is it?",
    ],
)
async def test_a_compound_question_still_goes_to_the_model(asked: str) -> None:
    value, properties = await deliver(asked)
    assert value == STATUS and properties is None


async def test_an_unusable_result_goes_to_the_model_as_it_is() -> None:
    value, properties = await deliver("How hot is the Pi?", result="{}")
    assert value == "{}" and properties is None


async def test_the_routers_own_call_gets_the_raw_result() -> None:
    value, properties = await deliver("How hot is the Pi?", call_id="router")
    assert value == STATUS and properties is None


def test_single_question() -> None:
    assert single_question("How hot is the Pi and is the speaker connected?")
    assert not single_question("How hot is the Pi and what is the weather?")


def test_the_pi_address_comes_from_the_environment() -> None:
    assert pi_url_from_env({"PI_DEVICE_URL": "http://169.254.9.9:8790/mcp"}) == (
        "http://169.254.9.9:8790/mcp"
    )
    assert pi_url_from_env({}) == PI_URL_FALLBACK
    assert pi_url_from_env({"PI_DEVICE_URL": ""}) == PI_URL_FALLBACK
