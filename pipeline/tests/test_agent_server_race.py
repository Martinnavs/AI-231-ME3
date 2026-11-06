import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from voice_pipeline import agent_server
from voice_pipeline.agent_server import AgentServer
from voice_pipeline.router import Routed, spec

PASSAGE = {"source": "road.txt", "text": "Billy Ray sold the truck for 450 dollars in Nashville."}
GOOD = "ANSWER: Billy Ray sold the truck.\nQUOTE: Billy Ray sold the truck"
WEB = "The web says Tom did."
USER = "who sold the truck?"


class Tool:
    def __init__(self, name: str, result: Any = None, delay: float = 0.0, boom: bool = False):
        self.name, self.result, self.delay, self.boom = name, result, delay, boom
        self.calls: list[dict[str, Any]] = []

    async def handler(self, params: Any) -> None:
        self.calls.append(dict(params.arguments))
        await asyncio.sleep(self.delay)
        if self.boom:
            raise RuntimeError("tool down")
        await params.result_callback(self.result)


def make(
    tmp_path: Path,
    reply: str | None = GOOD,
    web: Tool | None = None,
    docs: Tool | None = None,
    request: Any = None,
    with_docs: bool = True,
) -> tuple[AgentServer, Tool, Tool, list[str]]:
    web = web or Tool("web_answer", WEB)
    docs = docs or Tool("search_docs", [PASSAGE])
    tools = type("Tools", (), {})()
    tools.standard_tools = [web, docs] if with_docs else [web]
    asked: list[str] = []

    async def default_request(system: str, content: str) -> str:
        asked.append(content)
        assert reply is not None
        return reply

    server = AgentServer(
        None,
        [],
        tmp_path / "log.jsonl",
        tools,
        doc_request=(request or default_request) if with_docs else None,
    )
    return server, web, docs, asked


def routed(text: str = "web_answer|who sold the truck?") -> Routed:
    return Routed(spec.parse_output(text), text, 0.1, None, USER)


async def run(server: AgentServer, r: Routed | None = None, on_tool: Any = None):
    said: list[str] = []

    async def on_text(text: str) -> None:
        said.append(text)

    done = await server.run_routed(r or routed(), 0.0, on_text, on_tool=on_tool)
    return done, said


async def test_docs_accepted_speaks_docs_not_web(tmp_path):
    server, web, docs, asked = make(tmp_path, web=Tool("web_answer", WEB, delay=0.2))
    done, said = await run(server)
    assert said == ["Billy Ray sold the truck."]
    assert docs.calls == [{"query": USER}]
    assert "Question: who sold the truck?" in asked[0]
    assert done["race"]["docs"] == "accepted" and done["race"]["winner"] == "docs"
    assert done["race"]["doc_s"] >= 0
    json.dumps(done)


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        ("NONE", "none"),
        ("ANSWER: It was Tom who sold it.\nQUOTE: Billy Ray sold the truck", "check_failed"),
    ],
)
async def test_not_accepted_speaks_web(tmp_path, reply, status):
    server, *_ = make(tmp_path, reply=reply)
    done, said = await run(server)
    assert said == [WEB]
    assert done["race"]["docs"] == status and done["race"]["winner"] == "web"


async def test_doc_request_error_speaks_web(tmp_path):
    async def request(system, content):
        raise RuntimeError("http 500")

    server, *_ = make(tmp_path, request=request)
    done, said = await run(server)
    assert said == [WEB] and done["race"]["docs"] == "error"


async def test_search_docs_error_speaks_web(tmp_path):
    server, *_ = make(tmp_path, docs=Tool("search_docs", boom=True))
    done, said = await run(server)
    assert said == [WEB] and done["race"]["docs"] == "error"


async def test_slow_doc_answer_times_out_to_web(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_server, "DOC_ANSWER_TIMEOUT_S", 0.05)

    async def slow(system, content):
        await asyncio.sleep(1)
        return GOOD

    server, *_ = make(tmp_path, request=slow)
    done, said = await run(server)
    assert said == [WEB]
    assert done["race"]["docs"] == "timeout" and done["race"]["winner"] == "web"


async def test_docs_accepted_but_web_raises(tmp_path):
    server, *_ = make(tmp_path, web=Tool("web_answer", boom=True))
    done, said = await run(server)
    assert said == ["Billy Ray sold the truck."] and done["race"]["winner"] == "docs"


async def test_docs_accepted_with_web_still_running_is_cancelled(tmp_path):
    server, *_ = make(tmp_path, web=Tool("web_answer", WEB, delay=30))
    done, said = await asyncio.wait_for(run(server), 2)
    assert said == ["Billy Ray sold the truck."]


async def test_both_fail_returns_none(tmp_path):
    server, *_ = make(tmp_path, reply="NONE", web=Tool("web_answer", "I couldn't find that."))
    r = routed()
    done, said = await run(server, r)
    assert done is None and said == []
    assert r.race == {"docs": "none", "doc_s": r.race["doc_s"], "winner": None}


async def test_web_raising_with_docs_none_returns_none(tmp_path):
    server, *_ = make(tmp_path, reply="NONE", web=Tool("web_answer", boom=True))
    done, said = await run(server)
    assert done is None and said == []


async def test_no_search_docs_no_race(tmp_path):
    server, web, _, asked = make(tmp_path, with_docs=False)
    done, said = await run(server)
    assert said == [WEB] and "race" not in done and asked == []
    assert web.calls == [{"query": USER}]


async def test_no_race_for_a_mixed_turn(tmp_path):
    server, *_ = make(tmp_path)
    server.tools["smart_home"] = Tool("smart_home", {"ok": True})
    done, said = await run(server, routed("web_answer|who sold the truck?\nLIGHT_ON"))
    assert "race" not in done and WEB in said[0]


async def test_ack_once_when_nothing_spoken(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_server, "LOOKUP_ACK_S", 0.02)

    async def slow(system, content):
        await asyncio.sleep(0.15)
        return "NONE"

    server, *_ = make(tmp_path, request=slow, web=Tool("web_answer", WEB, delay=0.1))
    acks: list[tuple[str, bool]] = []

    async def on_tool(name: str, said: bool) -> None:
        acks.append((name, said))

    done, said = await run(server, on_tool=on_tool)
    assert acks == [("web_answer", False)] and said == [WEB]


async def test_no_ack_after_docs_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_server, "LOOKUP_ACK_S", 0.1)
    server, *_ = make(tmp_path, web=Tool("web_answer", WEB, delay=1))
    acks: list[tuple[str, bool]] = []

    async def on_tool(name: str, said: bool) -> None:
        acks.append((name, said))

    done, said = await run(server, on_tool=on_tool)
    await asyncio.sleep(0.2)
    assert acks == [] and said == ["Billy Ray sold the truck."]


STATUS_JSON = json.dumps(
    {
        "cpu_temperature_c": 54.5,
        "uptime_minutes": 22,
        "free_memory_mb": 1730,
        "speaker_connected": True,
    }
)


def with_device(tmp_path: Path, result: Any) -> AgentServer:
    server, *_ = make(tmp_path, with_docs=False)
    server.tools["device_status"] = Tool("device_status", result)
    return server


async def test_device_status_is_said_as_a_sentence(tmp_path):
    server = with_device(tmp_path, STATUS_JSON)
    r = Routed(
        spec.parse_output("device_status"), "device_status", 0.1, None, "what's the pi temperature?"
    )
    done, said = await run(server, r)
    assert said == ["The Pi is at 54 degrees Celsius."]
    assert done["routed"] is True


async def test_device_status_without_usable_numbers_goes_to_gemini(tmp_path):
    server = with_device(tmp_path, "{}")
    r = Routed(spec.parse_output("device_status"), "device_status", 0.1, None, "how is the pi?")
    done, said = await run(server, r)
    assert done is None and said == []


async def test_the_routers_question_reaches_the_lookup_and_the_documents(tmp_path):
    server, web, docs, _ = make(tmp_path)
    r = Routed(
        spec.parse_output("web_answer|Who sold the truck in 2022?"),
        "x",
        0.1,
        None,
        "and who sold it then?",
    )
    await run(server, r)
    assert web.calls == [{"query": "Who sold the truck in 2022?"}]
    assert docs.calls == [{"query": "Who sold the truck in 2022?"}]


async def run_say(server: AgentServer, r: Routed, on_tool: Any = None):
    events: list[tuple[str, str]] = []

    async def on_text(text: str) -> None:
        events.append(("text", text))

    async def on_say(text: str) -> None:
        events.append(("say", text))

    done = await server.run_routed(r, 0.0, on_text, on_tool=on_tool, on_say=on_say)
    return done, events


async def test_a_routed_lookup_speaks_the_sentence_before_the_answer(tmp_path):
    for with_docs in (True, False):
        server, *_ = make(tmp_path, with_docs=with_docs)
        done, events = await run_say(server, routed())
        assert [k for k, _ in events] == ["say", "text"]
        assert events[0][1] == agent_server.LOOKUP_SAY + " "


async def test_a_routed_turn_that_is_not_a_lookup_speaks_no_sentence(tmp_path):
    server, *_ = make(tmp_path)
    server.tools["smart_home"] = Tool("smart_home", {"ok": True})
    for text in ("LIGHT_ON", "none|Hello there."):
        done, events = await run_say(server, routed(text))
        assert [k for k, _ in events] == ["text"], text


async def test_no_sentence_without_a_say_channel(tmp_path):
    server, *_ = make(tmp_path)
    done, said = await run(server)
    assert agent_server.LOOKUP_SAY not in " ".join(said)


async def test_the_slow_lookup_ack_says_a_sentence_was_spoken_so_no_second_one_plays(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_server, "LOOKUP_ACK_S", 0.02)
    server, *_ = make(tmp_path, with_docs=False, web=Tool("web_answer", WEB, delay=0.1))
    acks: list[tuple[str, bool]] = []

    async def on_tool(name: str, said: bool) -> None:
        acks.append((name, said))

    done, events = await run_say(server, routed(), on_tool)
    assert acks == [("web_answer", True)]
    assert [k for k, _ in events] == ["say", "text"]


async def test_the_sentence_is_spoken_once_for_two_lookups(tmp_path):
    server, *_ = make(tmp_path, with_docs=False)
    done, events = await run_say(
        server, routed("web_answer|who sold the truck?\nweb_answer|who bought it?")
    )
    assert [k for k, _ in events].count("say") <= 1


async def test_a_slow_routed_play_music_says_a_sentence_first(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_server, "MUSIC_SAY_S", 0.02)
    server, *_ = make(tmp_path)
    server.tools["smart_home"] = Tool("smart_home", "Playing Some Song.", delay=0.1)
    done, events = await run_say(server, routed("PLAY_MUSIC"))
    assert [k for k, _ in events] == ["say", "text"]
    assert events[0][1] == agent_server.MUSIC_SAY + " "


async def test_a_quick_routed_play_music_says_no_sentence(tmp_path):
    server, *_ = make(tmp_path)
    server.tools["smart_home"] = Tool("smart_home", "Resuming.")
    done, events = await run_say(server, routed("PLAY_MUSIC"))
    assert [k for k, _ in events] == ["text"]
