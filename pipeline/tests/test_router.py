import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from voice_pipeline.agent_server import AgentServer
from voice_pipeline.hedge import HedgedAgent, HedgeGate
from voice_pipeline.router import LocalRouter

BOOK = {"Anna": "sister", "Ben": "brother"}


def router_for(reply: str | None, status: int = 200) -> LocalRouter:
    """A router whose server always answers `reply` (None: the connection fails)."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if reply is None:
            raise httpx.ConnectError("down")
        body = {"choices": [{"message": {"content": reply}}]}
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))
    found = LocalRouter("http://x", BOOK, client=client)
    found.seen = seen  # type: ignore[attr-defined]
    return found


class FakeAgent:
    def __init__(self) -> None:
        self.asked: list[Any] = []

    async def ask(self, messages: Any, **kwargs: Any) -> dict[str, Any]:
        self.asked.append(messages)
        await kwargs["on_text"]("From Gemini.")
        return {"status": "ok", "text": "From Gemini.", "first_text_s": 0.9, "end_s": 1.0}

    async def cancel(self) -> None:
        pass


def fake_tools(record: list[dict[str, Any]], home_ok: bool = True) -> Any:
    async def smart_home(params: Any) -> None:
        record.append(dict(params.arguments))
        result = {"ok": home_ok, "message": "Gary is not in the phonebook"}
        await params.result_callback(result)

    async def weather(params: Any) -> None:
        record.append({"tool": "weather", **params.arguments})
        await params.result_callback("It is 29 degrees in Baguio.")

    tools = [SimpleNamespace(name="smart_home", handler=smart_home)]
    tools.append(SimpleNamespace(name="weather", handler=weather))
    return SimpleNamespace(standard_tools=tools)


def server_for(reply: str | None, tmp_path: Path, home_ok: bool = True) -> tuple[AgentServer, Any]:
    record: list[dict[str, Any]] = []
    agent = FakeAgent()
    server = AgentServer(
        agent, record, tmp_path / "log", fake_tools(record, home_ok), router_for(reply)
    )
    server.calls = record  # type: ignore[attr-defined]
    return server, agent


async def turn(server: AgentServer, text: str, earlier: list[dict[str, str]] | None = None) -> Any:
    spoken: list[str] = []

    async def on_text(t: str) -> None:
        spoken.append(t)

    async def nothing(*a: Any) -> None:
        pass

    messages = [*(earlier or []), {"role": "user", "content": text}]
    result = await server.routed_turn(messages, on_text, nothing, on_text)
    return result, spoken


async def test_home_command_is_run_and_confirmed_without_gemini(tmp_path: Path) -> None:
    server, agent = server_for("TIMER|45 minutes", tmp_path)
    result, spoken = await turn(server, "set a timer for forty five minutes please")
    assert spoken == ["Timer set for 45 minutes."]
    assert server.calls == [{"intent": "TIMER", "slot": "45 minutes"}]  # type: ignore[attr-defined]
    assert agent.asked == []
    assert result["routed"] and result["router_lines"] == "TIMER|45 minutes"


async def test_escalate_hands_the_turn_to_gemini_and_logs_why(tmp_path: Path) -> None:
    server, agent = server_for("escalate|tools", tmp_path)
    result, spoken = await turn(server, "summarize my notes")
    assert spoken == ["From Gemini."] and len(agent.asked) == 1
    assert result["router_reason"] == "escalate" and "routed" not in result


async def test_server_down_or_garbled_line_falls_back(tmp_path: Path) -> None:
    for reply in (None, "fly_to_moon|now"):
        server, agent = server_for(reply, tmp_path)
        result, _ = await turn(server, "turn on the lights")
        assert len(agent.asked) == 1 and result["router_reason"]


async def test_none_reply_is_spoken_and_remembered_for_the_next_turn(tmp_path: Path) -> None:
    server, agent = server_for("none|You're welcome.", tmp_path)
    result, spoken = await turn(server, "thanks")
    assert spoken == ["You're welcome."]
    earlier = [
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": "You're welcome."},
        {"role": "user", "content": "what did Gemini say"},
        {"role": "assistant", "content": "A long answer\nin two lines."},
    ]
    assert server.history([*earlier, {"role": "user", "content": "and now"}]) == [
        ("what did Gemini say", "none|A long answer in two lines."),
    ]
    assert server.history(earlier[:2] + [{"role": "user", "content": "and now"}]) == [
        ("thanks", "none|You're welcome.")
    ]  # a reply the router wrote keeps its lines


async def test_info_tool_runs_and_its_sentence_is_spoken(tmp_path: Path) -> None:
    server, _ = server_for("weather|Baguio|today", tmp_path)
    _, spoken = await turn(server, "weather in Baguio")
    assert spoken == ["It is 29 degrees in Baguio."]


async def test_failed_home_command_says_why_and_stops(tmp_path: Path) -> None:
    server, _ = server_for("CALL|Gary\nLIGHT_ON", tmp_path, home_ok=False)
    _, spoken = await turn(server, "call Gary and lights on")
    assert spoken == ["Sorry, that didn't work: Gary is not in the phonebook."]
    assert len(server.calls) == 1  # type: ignore[attr-defined]


async def test_unsupported_names_and_bad_zones_go_to_gemini(tmp_path: Path) -> None:
    for reply in ("LIST_REMINDERS", "device_status", "get_current_time|Mars/Base", "bash|ls"):
        server, agent = server_for(reply, tmp_path)
        await turn(server, "whatever")
        assert len(agent.asked) == 1, reply


async def test_time_in_another_zone_is_answered_locally(tmp_path: Path) -> None:
    server, agent = server_for("get_current_time|Europe/London", tmp_path)
    _, spoken = await turn(server, "what time is it in London")
    assert agent.asked == [] and spoken[0].startswith("It's ") and spoken[0].endswith("in London.")


async def test_failed_lookup_goes_to_gemini_unless_something_already_changed(
    tmp_path: Path,
) -> None:
    server, agent = server_for("wiki_summary|Atlantis", tmp_path)

    async def missing(params: Any) -> None:
        await params.result_callback("I couldn't find a Wikipedia article about Atlantis.")

    server.tools["wiki_summary"] = SimpleNamespace(name="wiki_summary", handler=missing)
    result, spoken = await turn(server, "who is Atlantis")
    assert spoken == ["From Gemini."] and result["router_reason"].startswith("lookup failed")
    server, agent = server_for("LIGHT_ON\nwiki_summary|Atlantis", tmp_path)
    server.tools["wiki_summary"] = SimpleNamespace(name="wiki_summary", handler=missing)
    _, spoken = await turn(server, "lights on and who is Atlantis")
    assert agent.asked == [] and spoken == [
        "The lights are on. I couldn't find a Wikipedia article about Atlantis."
    ]


async def test_long_chat_goes_to_gemini_but_pleasantries_stay_local(tmp_path: Path) -> None:
    server, agent = server_for("none|The interrupt feature is not working.", tmp_path)
    result, _ = await turn(server, "I tried to interrupt but you did not trigger it why")
    assert len(agent.asked) == 1 and result["router_reason"] == "chat too long"
    server, agent = server_for("none|You're welcome.", tmp_path)
    _, spoken = await turn(server, "Thanks a lot.")
    assert agent.asked == [] and spoken == ["You're welcome."]


async def test_invented_long_reply_goes_to_gemini(tmp_path: Path) -> None:
    reply = "none|Middle aspiration is a condition where the vocal cords are partially closed."
    server, agent = server_for(reply, tmp_path)
    result, spoken = await turn(server, "Middle aspiration.")
    assert spoken == ["From Gemini."] and result["router_reason"] == "chat reply too long"


async def test_a_date_question_is_answered_with_the_date_not_the_time(tmp_path: Path) -> None:
    server, agent = server_for("get_current_time", tmp_path)
    _, spoken = await turn(server, "What's the day's date?")
    assert agent.asked == [] and spoken[0].startswith("Today is ") and "PM." not in spoken[0]
    _, spoken = await turn(server, "What's the date and time?")
    assert spoken[0].startswith("It's ") and "Today is " in spoken[0]


async def test_the_date_in_another_zone_names_the_zone(tmp_path: Path) -> None:
    server, _ = server_for("get_current_time|Asia/Tokyo", tmp_path)
    _, spoken = await turn(server, "What is today's date in Tokyo?")
    assert spoken[0].startswith("Today is ") and spoken[0].endswith("in Tokyo.")


async def test_a_question_that_wants_a_decision_is_not_answered_with_a_forecast(
    tmp_path: Path,
) -> None:
    server, agent = server_for("weather|Davao|tomorrow", tmp_path)
    result, spoken = await turn(server, "Should I bring an umbrella in Davao tomorrow?")
    assert spoken == ["From Gemini."] and result["router_reason"] == "needs reasoning"
    assert server.calls == []  # type: ignore[attr-defined]  # nothing was looked up locally
    _, spoken = await turn(server, "What's the weather in Davao tomorrow?")
    assert spoken == ["It is 29 degrees in Baguio."]


async def test_the_trained_offer_for_a_feeling_is_spoken_not_sent_to_gemini(
    tmp_path: Path,
) -> None:
    reply = "none|That sounds chilly. Want me to warm the room?"
    server, agent = server_for(reply, tmp_path)
    _, spoken = await turn(server, "I'm feeling cold.")
    assert agent.asked == [] and spoken == ["That sounds chilly. Want me to warm the room?"]
    assert server.calls == []  # type: ignore[attr-defined]  # the thermostat is not touched


class SlowGemini:
    """A model request: says `text` after `delay`, unless cancelled first."""

    def __init__(self, delay: float, text: str = "From Gemini.") -> None:
        self.delay, self.text = delay, text
        self.cancelled = asyncio.Event()
        self.said = False

    async def ask(
        self, messages: Any, *, reset: bool, on_text: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            await asyncio.wait_for(self.cancelled.wait(), self.delay)
        except TimeoutError:
            self.said = True
            await on_text(self.text)
        return {"status": "ok", "text": self.text, "first_text_s": self.delay, "end_s": self.delay}

    async def cancel(self) -> None:
        self.cancelled.set()


def race_for(
    reply: str, tmp_path: Path, delay: float, home_ok: bool = True, hold_s: float = 1.0
) -> Any:
    record: list[dict[str, Any]] = []
    gemini = [SlowGemini(delay), SlowGemini(delay)]
    agent = HedgedAgent(gemini, HedgeGate(), hold_s=hold_s)  # type: ignore[arg-type]
    server = AgentServer(
        agent, record, tmp_path / "log", fake_tools(record, home_ok), router_for(reply)
    )
    return server, gemini, record


async def test_router_wins_the_race_and_cancels_gemini(tmp_path: Path) -> None:
    server, gemini, record = race_for("LIGHT_ON", tmp_path, delay=2.0)
    result, spoken = await turn(server, "turn on the lights")
    assert spoken == ["The lights are on."] and record == [{"intent": "LIGHT_ON"}]
    assert result["hedge_winner"] == "router" and not any(g.said for g in gemini)


async def test_router_beats_a_quick_model_filler_while_it_is_deciding(tmp_path: Path) -> None:
    server, gemini, record = race_for("LIGHT_ON", tmp_path, delay=0.0)  # Gemini speaks at once
    router = server.router
    original = router.route

    async def late(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.2)
        return await original(*args, **kwargs)

    router.route = late  # type: ignore[method-assign]
    result, spoken = await turn(server, "turn on the lights")
    assert spoken == ["The lights are on."] and record == [{"intent": "LIGHT_ON"}]
    assert result["hedge_winner"] == "router"


async def test_gemini_wins_when_the_router_is_slower_than_the_hold(tmp_path: Path) -> None:
    server, gemini, record = race_for("LIGHT_ON", tmp_path, delay=0.0, hold_s=0.1)
    router = server.router
    original = router.route

    async def late(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.4)
        return await original(*args, **kwargs)

    router.route = late  # type: ignore[method-assign]
    result, spoken = await turn(server, "turn on the lights")
    assert spoken == ["From Gemini."] and record == []  # the router never ran the light
    assert result["hedge_winner"] in (0, 1)


async def test_unusable_router_answer_leaves_the_race_to_gemini(tmp_path: Path) -> None:
    server, gemini, record = race_for("escalate|tools", tmp_path, delay=0.3)
    result, spoken = await turn(server, "summarize my notes")
    assert spoken == ["From Gemini."] and result["router_reason"] == "escalate"
    assert result["router_lines"] == "escalate|tools" and record == []


class DeadGemini:
    """No network: the request ends at once without saying anything."""

    async def ask(self, messages: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "error", "text": "", "first_text_s": None, "end_s": 0.0}

    async def cancel(self) -> None:
        pass


async def test_router_answers_when_the_model_requests_fail(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    agent = HedgedAgent([DeadGemini(), DeadGemini()], HedgeGate(), hold_s=0)  # type: ignore[list-item]
    server = AgentServer(
        agent, record, tmp_path / "log", fake_tools(record), router_for("LIGHT_ON")
    )
    original = server.router.route  # type: ignore[union-attr]

    async def late(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.2)  # the router is slower than the failure
        return await original(*args, **kwargs)

    server.router.route = late  # type: ignore[union-attr,method-assign]
    result, spoken = await turn(server, "turn on the lights")
    assert spoken == ["The lights are on."] and result["hedge_winner"] == "router"


async def test_plain_race_lets_a_fast_model_win_without_waiting(tmp_path: Path) -> None:
    record: list[dict[str, Any]] = []
    gemini = [SlowGemini(0.0), SlowGemini(0.0)]
    agent = HedgedAgent(gemini, HedgeGate(), hold_s=0)  # type: ignore[arg-type]
    server = AgentServer(
        agent, record, tmp_path / "log", fake_tools(record), router_for("LIGHT_ON")
    )
    original = server.router.route  # type: ignore[union-attr]

    async def late(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.2)
        return await original(*args, **kwargs)

    server.router.route = late  # type: ignore[union-attr,method-assign]
    result, spoken = await turn(server, "turn on the lights")
    assert spoken == ["From Gemini."] and record == []


async def test_transient_lookup_failure_is_retried_once(tmp_path: Path) -> None:
    server, agent = server_for("weather|Cebu|today", tmp_path)
    answers = [
        "The weather service did not answer, so I can't say right now.",
        "Today in Cebu: rain.",
    ]
    calls: list[str] = []

    async def flaky(params: Any) -> None:
        calls.append("weather")
        await params.result_callback(answers[len(calls) - 1])

    server.tools["weather"] = SimpleNamespace(name="weather", handler=flaky)
    _, spoken = await turn(server, "weather in Cebu")
    assert calls == ["weather", "weather"] and spoken == ["Today in Cebu: rain."]
    assert agent.asked == []


async def test_lookup_that_keeps_failing_goes_to_gemini_after_one_retry(tmp_path: Path) -> None:
    server, agent = server_for("weather|Cebu|today", tmp_path)
    calls: list[str] = []

    async def down(params: Any) -> None:
        calls.append("weather")
        await params.result_callback(
            "The weather service did not answer, so I can't say right now."
        )

    server.tools["weather"] = SimpleNamespace(name="weather", handler=down)
    result, spoken = await turn(server, "weather in Cebu")
    assert len(calls) == 2 and spoken == ["From Gemini."]
    assert result["router_reason"].startswith("lookup failed")


async def test_a_missing_article_is_not_retried(tmp_path: Path) -> None:
    server, _ = server_for("wiki_summary|Atlantis", tmp_path)
    calls: list[str] = []

    async def missing(params: Any) -> None:
        calls.append("wiki")
        await params.result_callback("I couldn't find a Wikipedia article about Atlantis.")

    server.tools["wiki_summary"] = SimpleNamespace(name="wiki_summary", handler=missing)
    await turn(server, "who is Atlantis")
    assert calls == ["wiki"]


async def test_extra_field_on_a_home_command_is_folded_into_its_slot(tmp_path: Path) -> None:
    server, _ = server_for("ALARM|6:30 AM|tomorrow", tmp_path)
    _, spoken = await turn(server, "set an alarm for 6:30 tomorrow")
    assert spoken == ["Alarm set for 6:30 AM tomorrow."]
    assert server.calls[0] == {"intent": "ALARM", "slot": "6:30 AM tomorrow"}  # type: ignore[attr-defined]


async def test_a_reply_that_parrots_a_garbled_turn_goes_to_gemini(tmp_path: Path) -> None:
    server, agent = server_for("none|Bucket on Mu.", tmp_path)
    result, spoken = await turn(server, "Bucket on Mu.")
    assert spoken == ["From Gemini."] and result["router_reason"] == "echo"


async def test_a_short_pleasantry_that_matches_the_turn_stays_local(tmp_path: Path) -> None:
    server, agent = server_for("none|Hello!", tmp_path)
    _, spoken = await turn(server, "Hello.")
    assert spoken == ["Hello!"] and agent.asked == []


async def test_wiki_summary_lookups_use_the_web_answer_tool_when_it_exists(tmp_path: Path) -> None:
    server, _ = server_for("wiki_summary|Cormac McCarthy", tmp_path)
    seen: list[dict[str, Any]] = []

    async def web_answer(params: Any) -> None:
        seen.append(dict(params.arguments))
        await params.result_callback("Cormac McCarthy was an American novelist.")

    async def wiki(params: Any) -> None:  # registered too, as on the real server
        raise AssertionError("the encyclopedia tool must not be called directly")

    server.tools["wiki_summary"] = SimpleNamespace(name="wiki_summary", handler=wiki)
    server.tools["web_answer"] = SimpleNamespace(name="web_answer", handler=web_answer)
    _, spoken = await turn(server, "who is Cormac McCarthy")
    assert seen == [{"query": "who is Cormac McCarthy"}]  # the whole question, not just the topic
    assert spoken == ["Cormac McCarthy was an American novelist."]

    server, _ = server_for("LIGHT_OFF\nwiki_summary|The Road", tmp_path)
    server.tools["wiki_summary"] = SimpleNamespace(name="wiki_summary", handler=wiki)
    server.tools["web_answer"] = SimpleNamespace(name="web_answer", handler=web_answer)
    seen.clear()
    await turn(server, "turn off the lights and tell me about The Road")
    assert seen == [{"query": "The Road"}]  # a compound turn: only the topic
