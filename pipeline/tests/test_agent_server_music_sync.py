"""The client's own pause (the user spoke over the music) reaches the music tool silently."""

import json
from typing import Any

from voice_pipeline.agent_server import AgentServer


class MusicTool:
    name = "music"

    def __init__(self, boom: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.boom = boom

    async def handler(self, params: Any) -> None:
        self.calls.append(dict(params.arguments))
        if self.boom:
            raise RuntimeError("down")
        await params.result_callback(json.dumps({"ok": True, "sentence": "Paused."}))


def server(tool: MusicTool | None) -> AgentServer:
    tools = type("Tools", (), {})()
    tools.standard_tools = [tool] if tool else []
    return AgentServer(None, [], None, tools)  # type: ignore[arg-type]


async def test_a_paused_flag_calls_the_music_tools_pause_and_says_nothing() -> None:
    tool = MusicTool()
    assert await server(tool).sync_music_paused() is None
    assert tool.calls == [{"action": "pause", "query": ""}]


async def test_a_failing_music_tool_does_not_break_the_request() -> None:
    tool = MusicTool(boom=True)
    await server(tool).sync_music_paused()
    assert len(tool.calls) == 1


async def test_without_a_music_tool_the_flag_is_ignored() -> None:
    await server(None).sync_music_paused()
