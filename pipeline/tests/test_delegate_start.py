"""R2 and R4: a started job is answered with a fixed sentence; a Gemini stall is retried once."""

from types import SimpleNamespace
from typing import Any

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import FunctionCallResultFrame, FunctionCallResultProperties

from voice_pipeline import agent
from voice_pipeline.agent import TurnTap, stall_sentence
from voice_pipeline.agent_tools import DELEGATE_STARTED, _recorded, job_started

STARTED = '{"job":"job20","status":"started","message":"It is running in the background."}'


def test_only_a_started_job_counts() -> None:
    assert job_started(STARTED) and job_started({"status": "started"})
    assert not job_started('{"job":"job1","status":"done","summary":"ok"}')
    assert not job_started({"status": "busy"}) and not job_started("not json")


async def _delegate_properties(result: Any) -> tuple[Any, Any]:
    seen: list[Any] = []

    async def handler(params: Any) -> None:
        await params.result_callback(result)

    async def result_callback(value: Any, *, properties: Any = None) -> None:
        seen.append((value, properties))

    schema = FunctionSchema(
        name="delegate", description="d", properties={}, required=[], handler=handler
    )
    wrapped = _recorded(schema, [])
    assert wrapped.handler is not None
    await wrapped.handler(SimpleNamespace(arguments={}, result_callback=result_callback))
    return seen[0]


async def test_a_started_job_gets_the_fixed_sentence_and_no_second_request() -> None:
    value, properties = await _delegate_properties(STARTED)
    assert value == DELEGATE_STARTED and properties.run_llm is False
    assert "minutes" not in value and "{" not in value


async def test_a_job_that_finished_in_time_still_goes_to_the_model() -> None:
    done = '{"job":"job1","status":"done","summary":"41"}'
    value, properties = await _delegate_properties(done)
    assert value == done and properties is None


def _result(result: Any) -> FunctionCallResultFrame:
    return FunctionCallResultFrame(
        function_name="delegate",
        tool_call_id="1",
        arguments={},
        result=result,
        properties=FunctionCallResultProperties(run_llm=False),
    )


async def _tap(said: bool, held: str = "") -> tuple[TurnTap, list[str]]:
    texts: list[str] = []

    async def on_text(t: str) -> None:
        texts.append(t)

    tap = TurnTap()
    tap.begin(on_text)
    tap.calls_started, tap.said, tap.say_text = 1, said, held
    return tap, texts


async def test_the_start_sentence_follows_the_models_own_line_once() -> None:
    tap, texts = await _tap(True, "I am starting a job to count the lines.")  # still held
    await tap._result(_result(DELEGATE_STARTED))
    assert texts == ["I am starting a job to count the lines. ", DELEGATE_STARTED + " "]
    assert tap.done.is_set()


async def test_when_the_line_was_already_spoken_only_the_sentence_is_added() -> None:
    tap, texts = await _tap(True)
    await tap._result(_result(DELEGATE_STARTED))
    assert texts == [DELEGATE_STARTED + " "]


async def test_without_any_say_it_says_that_the_job_started() -> None:
    tap, texts = await _tap(False)
    await tap._result(_result(DELEGATE_STARTED))
    assert texts == ["Started. " + DELEGATE_STARTED + " "]


def test_a_stall_after_a_tool_never_speaks_its_json() -> None:
    assert stall_sentence("delegate_status", STARTED, "is it done") == agent.STALL_GENERIC
    assert stall_sentence("weather", "Sunny in Manila.", "x") == "Sunny in Manila."


def test_gemini_requests_are_retried_once_on_a_stall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "read_key", lambda _name: "k")
    llm = agent.build_llm("system", keepalive_s=None)
    assert llm._retry_on_timeout is True
    assert llm._retry_timeout_secs == agent.RETRY_FIRST_CHUNK_S == 5.0
    assert llm._stream_idle_timeout_secs == agent.STREAM_IDLE_S == 10.0
    # worst case 5 s + 10 s stays inside the client's 20 s cap
    assert agent.RETRY_FIRST_CHUNK_S + agent.STREAM_IDLE_S < 20.0
