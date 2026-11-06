import asyncio
from typing import Any

from voice_pipeline import agent_server
from voice_pipeline.agent_server import AgentServer, OnlineProbe


class NeverAgent:
    async def ask(self, *a: Any, **k: Any) -> dict:
        raise AssertionError("Gemini must not be asked while offline")

    async def cancel(self) -> None:
        return None


def test_a_gemini_turn_while_offline_is_answered_at_once(tmp_path) -> None:
    server = AgentServer(NeverAgent(), [], tmp_path / "log.jsonl")
    server.probe = OnlineProbe()
    server.probe.online = False
    said: list[str] = []

    async def on_text(t: str) -> None:
        said.append(t)

    out = asyncio.run(
        server.gemini([{"role": "user", "content": "what is 9 times 11"}], on_text, None, None)
    )
    assert said == [agent_server.OFFLINE_SAY] and out["offline"] is True


def test_the_probe_needs_two_failures_to_go_offline(monkeypatch) -> None:
    probe = OnlineProbe(every=0.0)
    results = iter([False, False, True])

    async def check() -> bool:
        return next(results)

    monkeypatch.setattr(probe, "check", check)
    seen: list[bool] = []

    async def run() -> None:
        for _ in range(3):
            ok = await probe.check()
            probe._fails = 0 if ok else probe._fails + 1
            probe.online = ok or probe._fails < 2
            seen.append(probe.online)

    asyncio.run(run())
    assert seen == [True, False, True]


def test_an_unmarked_alarm_is_a_morning_one() -> None:
    from voice_pipeline.call_policy import alarm_slot

    assert alarm_slot("7:15 PM", "wake me at seven fifteen tomorrow") == "7:15 AM"
    assert alarm_slot("7:15 PM", "set an alarm for seven fifteen tonight") == "7:15 PM"
    assert alarm_slot("12:30 PM", "alarm at twelve thirty") == "12:30 PM"
