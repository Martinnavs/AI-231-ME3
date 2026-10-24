"""The assistant's own words in front of an interruption (.scratch/echo-next/PLAN.md, E3).

The cases are transcripts logged in the A/B sessions of 2026-10-23 (docs/LIVE-AGENT.md).
"""

import argparse
import asyncio
import io
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from doc_chat.__main__ import Interruption, Session  # noqa: E402
from doc_chat.bargein import is_all_echo, strip_echo_prefix  # noqa: E402
from doc_chat.dialogue import ChatHistory  # noqa: E402
from doc_chat.speech import HeardTurn  # noqa: E402

PRETTY = "All the Pretty Horses is the first book in The Border Trilogy, published in 1992."
NO_COUNTRY = "No Country for Old Men is a 2005 border thriller that has become a classic."
BORN = "Cormac McCarthy was born Charles Joseph McCarthy Jr. in 1933 in Providence."
OUTER = "Outer Dark was Cormac McCarthy's second novel, published in 1968."
PASSENGER = "The Passenger is an interconnected novel published in 2022 about Bobby Western."
ROAD = "The Road is a post-apocalyptic novel published in 2006 about a father and son."
TIMER = "OK. I have stopped the music and set a timer for ten minutes."


def test_its_words_before_the_stop_are_dropped_with_the_stop() -> None:
    assert strip_echo_prefix(
        "First book in the Border Trilogy. Stop! What year was it published?", PRETTY
    ) == ("What year was it published?")
    assert strip_echo_prefix("2005 border. Stop. Set a timer for ten minutes.", NO_COUNTRY) == (
        "Set a timer for ten minutes."
    )
    assert strip_echo_prefix("Joseph McCartney Chief Stop, when did he die?", BORN) == (
        "When did he die?"
    )
    assert strip_echo_prefix("Second, novel stop pause the music.", OUTER) == "Pause the music."
    assert strip_echo_prefix(
        "Album published in twenty five Stop, Play Some Music.", PASSENGER
    ) == ("Play Some Music.")


def test_a_bare_stop_after_its_words_is_kept_for_the_stop_rules() -> None:
    assert strip_echo_prefix("Apocalyptic. Stop.", ROAD) == "Stop."


def test_what_the_user_says_is_left_alone() -> None:
    for text in (
        "Stop. What year was it published?",  # the stop comes first: nothing in front of it
        "Please stop playing music.",  # "please" is not the assistant's word
        "Set a timer for ten minutes.",  # no stop word
        "Tell me about the road and why the father keeps going on and on, stop.",  # too long
    ):
        assert strip_echo_prefix(text, ROAD) == text


def test_a_transcript_of_only_its_words_is_its_echo() -> None:
    assert is_all_echo("Novel published in 2007.", ROAD)  # one word misheard
    assert is_all_echo("Set a timer for a minute.", TIMER)  # it heard its own confirmation
    assert not is_all_echo("When was The Road published?", ROAD)  # its words, not its order
    assert not is_all_echo("Stop.", ROAD)  # too short to tell
    assert not is_all_echo("Turn on the lights.", ROAD)


def _session(**args: Any) -> tuple[Session, list[HeardTurn], list[str]]:
    heard: list[HeardTurn] = []
    answered: list[str] = []

    class Hub:
        async def blocks(self) -> Any:
            while True:
                await asyncio.sleep(3600)
                yield b""

    class Client:
        hub = Hub()
        noise_floor = 0.01

        async def listen_turn(self, **kw: Any) -> HeardTurn:
            audio = kw.get("audio")
            if audio is not None:
                got = 0
                async for _ in audio:
                    got += 1
                    if got >= 3:
                        break
            return heard.pop(0)

    ns = argparse.Namespace(
        play=False, voice="F1", slot="doc?", risk=None, vad_hold=0.6, barge_in=True,
        ask_to_add=False, **args,
    )  # fmt: skip
    s = Session(
        args=ns,
        client=Client(),  # type: ignore[arg-type]
        log_file=io.StringIO(),
        out_dir=None,
        history=ChatHistory(4),
    )

    async def respond(idx: int, turn: HeardTurn) -> Interruption | None:
        answered.append(turn.text)
        if len(answered) == 1:  # the first answer is talked over
            s._spoken = [NO_COUNTRY]
            return Interruption("barge_in", [bytes(3200)] * 3, cancel_ms=0.0, after_final_ms=0.0)
        return None

    s.respond = respond  # type: ignore[method-assign]
    return s, heard, answered


def _turn(text: str) -> HeardTurn:
    return HeardTurn(text, None, None, 1.0, 2.0, 1.5, b"", "", b"")


async def _run(s: Session) -> None:
    await asyncio.wait_for(s.run_mic(), timeout=10)


async def test_with_the_flag_the_command_reaches_the_model_without_its_words() -> None:
    s, heard, answered = _session(strip_echo_prefix=True)
    heard.extend(
        [
            _turn("Tell me about No Country for Old Men."),
            _turn("2005 border. Stop. Set a timer for ten minutes."),
            _turn("Quit."),
        ]
    )
    await _run(s)
    assert answered == ["Tell me about No Country for Old Men.", "Set a timer for ten minutes."]


async def test_without_the_flag_nothing_changes() -> None:
    s, heard, answered = _session()
    heard.extend(
        [
            _turn("Tell me about No Country for Old Men."),
            _turn("2005 border. Stop. Set a timer for ten minutes."),
            _turn("Quit."),
        ]
    )
    await _run(s)
    assert answered[1] == "2005 border. Stop. Set a timer for ten minutes."


async def test_with_the_flag_an_interruption_of_only_its_words_is_ignored() -> None:
    s, heard, answered = _session(strip_echo_prefix=True)
    heard.extend(
        [
            _turn("Tell me about No Country for Old Men."),
            _turn("Two thousand five border thriller that has become."),
            _turn("Quit."),
        ]
    )
    await _run(s)
    assert answered == ["Tell me about No Country for Old Men."]
