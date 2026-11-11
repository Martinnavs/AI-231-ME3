"""R2: a tool's JSON is never sent to speech."""

import json
from pathlib import Path
from typing import Any

from voice_pipeline.agent_server import AgentServer
from voice_pipeline.payload_guard import PayloadFilter


def _run(*pieces: str) -> tuple[str, PayloadFilter]:
    f = PayloadFilter()
    out = "".join(f.feed(p) for p in pieces) + "".join(f.finish())
    return out, f


def test_a_json_object_is_dropped_and_the_sentence_before_it_kept() -> None:
    out, f = _run('I am checking. {"job":"job20","status":"started","message":"It is running."}')
    assert out == "I am checking. " and len(f.dropped) == 1


def test_an_object_split_over_pieces_is_still_dropped() -> None:
    out, f = _run("Okay. {", '"job":"job2",', '"status":"st', 'arted"} Done.')
    assert out == "Okay.  Done."
    assert f.dropped == ['{"job":"job2","status":"started"}']


def test_nested_objects_and_whitespace_after_the_brace() -> None:
    out, _ = _run('A. { "a": {"b": 1}, "c": 2 } B.')
    assert out == "A.  B."


def test_ordinary_braces_and_text_pass() -> None:
    out, f = _run("Use {braces} like this, and { so }.")
    assert out == "Use {braces} like this, and { so }." and f.dropped == []


def test_a_lone_brace_at_the_end_is_given_back() -> None:
    out, _ = _run("Set {")
    assert out == "Set {"


def test_an_unfinished_object_is_dropped_at_the_end_of_the_turn() -> None:
    out, f = _run('Fine. {"job":"job1","sta')
    assert out == "Fine. " and len(f.dropped) == 1


class JsonAgent:
    async def ask(self, messages: Any, *, reset: bool, on_text: Any, **_: Any) -> dict:
        await on_text("I am checking.")
        await on_text(' {"job":"job20",')
        await on_text('"status":"started"}')
        return {"status": "ok", "text": "x", "first_call_s": 0.1, "first_text_s": 0.1, "end_s": 0.2}

    async def cancel(self) -> None:
        return None


async def test_the_server_never_sends_the_json_and_logs_the_drop(tmp_path: Path) -> None:
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    server = AgentServer(JsonAgent(), [], tmp_path / "log.jsonl")
    ws_server = await serve(server.handle, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(ws_server.sockets)).getsockname()[1]}"
    sent: list[dict[str, Any]] = []
    async with connect(url) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": "go"}]}))
        while True:
            event = json.loads(await ws.recv())
            sent.append(event)
            if event.get("done"):
                break
    assert "job20" not in json.dumps(sent) and sent[0] == {"text": "I am checking."}
    row = json.loads((tmp_path / "log.jsonl").read_text().splitlines()[0])
    assert row["dropped_payload"] == ['{"job":"job20","status":"started"}']
    ws_server.close()
    await ws_server.wait_closed()


def test_source_asides_are_dropped_even_when_they_arrive_in_pieces() -> None:
    from voice_pipeline.payload_guard import SourceFilter, strip_sources

    whole = "UP offers it (Source: coe.upd.edu.ph). Also a PhD (Sources: a.ph, b.ph) too (really)."
    for size in (1, 2, 3, 7, 200):
        f = SourceFilter()
        out = "".join(f.feed(whole[i : i + size]) for i in range(0, len(whole), size))
        out += "".join(f.finish())
        assert "Source" not in out and "ph)" not in out, (size, out)
        assert "(really)" in out and out.startswith("UP offers it") and "PhD" in out, (size, out)
    assert strip_sources(whole).count("(") == 1
    f = SourceFilter()
    assert f.feed("a (sou") == "a " and f.finish() == ["(sou"]
