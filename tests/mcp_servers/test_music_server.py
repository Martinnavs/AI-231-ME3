"""Y3: the Pi's music MCP server logic: actions, sentences, events, failures."""

import json
from typing import Any

import httpx

from voice_agent.mcp_servers.music import MusicControl, build_server, short_title

CFG = {"default_query": "relaxing music mix", "level_db": -6.0, "step_db": 3.0, "min_db": -30.0}
HITS = [
    {
        "title": f"Song {i} (Official Video) | Channel",
        "duration": 200,
        "stream_url": f"https://x/{i}",
        "id": str(i),
    }
    for i in range(3)
]


def control(results: Any = None, seen: list[str] | None = None) -> MusicControl:
    def search(q: str) -> list[dict[str, Any]]:
        if seen is not None:
            seen.append(q)
        if isinstance(results, Exception):
            raise results
        return HITS if results is None else results

    return MusicControl(search, dict(CFG))


def test_short_titles() -> None:
    assert (
        short_title("Taylor Swift - Guilty as Sin (Official Lyric Video)")
        == "Taylor Swift - Guilty as Sin"
    )
    assert (
        short_title("Relaxing Jazz for a Warm Autumn Morning \U0001f342 Cozy Porch Ambience")
        == "Relaxing Jazz for a Warm Autumn Morning"
    )
    assert short_title("A | B") == "A" and short_title("(Official)") == "your music"
    assert len(short_title("word " * 40)) <= 50


def test_play_searches_says_the_title_and_sends_the_stream_to_the_client() -> None:
    seen: list[str] = []
    out = control(seen=seen).act("play", "taylor swift")
    assert seen == ["taylor swift"] and out["ok"] and out["sentence"] == "Playing Song 0."
    assert out["event"] == {
        "event": "music",
        "action": "play",
        "url": "https://x/0",
        "title": "Song 0",
        "db": -6.0,
    }


def test_bare_play_uses_the_default_query_and_resumes_when_paused() -> None:
    seen: list[str] = []
    c = control(seen=seen)
    assert c.act("play")["sentence"] == "Playing Song 0." and seen == ["relaxing music mix"]
    c.act("pause")
    out = c.act("play")
    assert out["sentence"] == "Resuming." and out["event"]["action"] == "resume" and len(seen) == 1


def test_pause_resume_stop() -> None:
    c = control()
    c.act("play", "x")
    assert c.act("pause") == {
        "ok": True,
        "sentence": "Paused.",
        "event": {"event": "music", "action": "pause"},
    }
    assert c.act("pause")["sentence"] == "It is already paused."
    assert c.act("resume")["event"]["action"] == "resume"
    assert c.act("stop")["event"]["action"] == "stop" and c.state == "idle"


def test_next_plays_the_next_search_result_and_stops_at_the_last() -> None:
    c = control()
    c.act("play", "x")
    assert c.act("next")["sentence"] == "Playing Song 1."
    assert c.act("next")["event"]["url"] == "https://x/2"
    last = c.act("next")
    assert not last["ok"] and "last result" in last["sentence"] and "event" not in last


def test_volume_steps_and_clamps() -> None:
    c = control()
    c.act("play", "x")
    assert c.act("volume_up") == {
        "ok": True,
        "sentence": "Louder.",
        "event": {"event": "music", "action": "volume", "db": -3.0},
    }
    assert c.act("volume_up")["event"]["db"] == 0.0
    assert c.act("volume_up")["sentence"] == "That is as loud as the music goes."
    for _ in range(12):
        c.act("volume_down")
    assert (
        c.level_db == -30.0 and c.act("volume_down")["sentence"] == "That is as quiet as it goes."
    )


def test_when_nothing_plays_the_controls_say_so_and_do_nothing() -> None:
    c = control()
    for action in ("pause", "stop", "next", "volume_up", "volume_down"):
        assert c.act(action) == {"ok": False, "inactive": True, "sentence": ""}, action


def test_failures_are_sentences() -> None:
    assert control([]).act("play", "qzxv")["sentence"] == "I couldn't find any music for that."
    out = control(httpx.ConnectError("down")).act("play", "x")
    assert (
        not out["ok"]
        and out["sentence"] == "I can't reach the music search right now."
        and "event" not in out
    )
    bad = httpx.HTTPStatusError(
        "x", request=httpx.Request("GET", "http://x"), response=httpx.Response(400)
    )
    assert "only search for music by name" in control(bad).act("play", "http://x")["sentence"]
    err = httpx.HTTPStatusError(
        "x", request=httpx.Request("GET", "http://x"), response=httpx.Response(504)
    )
    assert "isn't working" in control(err).act("play", "x")["sentence"]
    assert not control().act("dance")["ok"]


async def test_the_one_tool_returns_json() -> None:
    from fastmcp import Client

    async with Client(build_server(control())) as client:
        tools = await client.list_tools()
        assert [t.name for t in tools] == ["music"]
        result = await client.call_tool("music", {"action": "play", "query": "jazz"})
        assert json.loads(result.data)["sentence"] == "Playing Song 0."
