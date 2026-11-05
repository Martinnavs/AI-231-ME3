"""fillers: rotation, timing per turn, and the reviewed phrase file."""

import asyncio

from doc_chat.fillers import Fillers, TurnFillers

CONFIG = {
    "timing": {"stall_s": 0.5, "notice_s": 2.5, "giveup_s": 8.0},
    "stall": ["Mm, one moment.", "Okay, let me see.", "Hmm, okay."],
    "notice": ["Sorry, this is taking a little longer than usual."],
    "giveup": ["Sorry, that's taking too long. Could you ask me again?"],
    "default": ["One moment."],
    "tools": {"weather": ["Let me check the weather.", "Checking the weather now."]},
}


def _ready() -> Fillers:
    f = Fillers(CONFIG)
    asyncio.run(f.synthesise(lambda line: _pcm(line)))
    return f


async def _pcm(line: str) -> bytes:
    return line.encode()


def test_lines_rotate_and_never_repeat_back_to_back() -> None:
    f = _ready()
    picked = [f.pick("stall") for _ in range(6)]
    texts = [p[0] for p in picked if p is not None]
    assert texts == ["Mm, one moment.", "Okay, let me see.", "Hmm, okay."] * 2
    assert all(a != b for a, b in zip(texts, texts[1:], strict=False))
    assert picked[0] == ("Mm, one moment.", b"Mm, one moment.")


def test_an_unknown_tool_uses_the_default_set_and_missing_audio_is_skipped() -> None:
    f = _ready()
    assert f.pick("tool:something_new") == ("One moment.", b"One moment.")
    f.audio.pop("Let me check the weather.")
    assert f.pick("tool:weather") == ("Checking the weather now.", b"Checking the weather now.")
    assert Fillers(CONFIG).pick("stall") is None  # nothing synthesised yet: no filler


def test_a_slow_turn_gets_stall_then_notice_then_giveup() -> None:
    turn = TurnFillers(_ready())
    assert turn.next_at() == 0.5 and turn.due(0.4) is None
    assert turn.due(0.5) == "stall" and turn.next_at() == 2.5
    assert turn.due(1.0) is None and turn.due(2.6) == "notice"
    assert turn.due(7.9) is None and turn.due(8.0) == "giveup"
    assert turn.next_at() is None and turn.due(30.0) is None


def test_a_tool_call_before_the_stall_replaces_it() -> None:
    turn = TurnFillers(_ready())
    assert turn.tool("weather") == "tool:weather"
    assert turn.tool("weather") is None  # one per turn
    assert turn.next_at() == 15.0  # progress shown: the one notice moves to 15 s


def test_nothing_plays_once_the_answer_has_started() -> None:
    turn = TurnFillers(_ready())
    turn.answered = True
    assert turn.next_at() is None and turn.due(9.0) is None and turn.tool("weather") is None


def test_the_reviewed_phrase_file_loads() -> None:
    f = Fillers.load()
    assert (f.stall_s, f.notice_s, f.giveup_s) == (1.8, 2.5, 8.0)
    assert len(f.sets["stall"]) == 7 and len(f.sets["giveup"]) == 5
    assert len(f.sets["tool:weather"]) == 7 and len(f.sets["default"]) == 7
    for name in (
        "smart_home",
        "get_current_time",
        "wiki_summary",
        "web_answer",
        "set_volume",
        "device_status",
    ):
        assert len(f.sets[f"tool:{name}"]) == 7, name


def test_filler_lines_are_stripped_from_anywhere_in_a_transcript() -> None:
    from doc_chat.fillers import strip_fillers

    lines = ["Almost there.", "Got it.", "Mm, one moment."]
    assert strip_fillers("two Nobel Prizes? Almost there. Got it.", lines) == "two Nobel Prizes?"
    assert (
        strip_fillers("Who won? mm, one moment and then", lines)
        == "Who won? mm, one moment and then"
    )
    assert strip_fillers("what is a got itself", lines) == "what is a got itself"
    assert strip_fillers("Almost there.", lines) == ""


def test_arrived_text_skips_the_stall_and_a_said_tool_defers_it() -> None:
    from doc_chat.fillers import Fillers, TurnFillers

    f = Fillers({"stall": ["s"], "notice": ["n"], "giveup": ["g"], "default": ["d"], "tools": {}})
    turn = TurnFillers(f)
    assert turn.tool("weather", said=True) is None and turn.say_pending  # the say comes first
    assert turn.due(1.5) == "stall"  # ... and a canned line may still follow it
    turn = TurnFillers(f)
    turn.text_arrived()
    assert turn.due(1.5) is None


def _real() -> TurnFillers:
    return TurnFillers(Fillers.load())


def test_with_nothing_back_the_turn_is_given_up_at_8_s() -> None:
    turn = _real()
    assert turn.due(1.8) == "stall" and turn.due(2.5) == "notice"
    assert turn.due(7.9) is None and turn.due(8.0) == "giveup"


def test_a_tool_event_at_2_s_moves_the_notice_to_15_s_and_the_give_up_to_20_s() -> None:
    turn = _real()
    assert turn.due(1.8) == "stall"
    turn.tool("web_answer")  # at 2 s
    assert turn.due(2.6) is None and turn.due(8.0) is None  # no early notice, no give-up
    assert turn.due(14.9) is None and turn.due(15.0) == "notice"
    assert turn.due(19.9) is None and turn.due(20.0) == "giveup"


def test_only_one_notice_when_progress_comes_after_it() -> None:
    turn = _real()
    assert turn.due(1.8) == "stall" and turn.due(2.5) == "notice"
    turn.tool("weather", said=True)  # at 3 s
    assert turn.due(15.0) is None and turn.due(19.9) is None and turn.due(20.0) == "giveup"


def test_a_delegate_start_has_its_own_shorter_cap() -> None:
    turn = _real()
    turn.tool("delegate")
    assert turn.next_at() == 12.0 and turn.due(12.0) == "giveup"  # the 15 s notice never comes


def test_once_text_has_started_the_turn_is_never_given_up() -> None:
    turn = _real()
    assert turn.due(1.8) == "stall"
    turn.text_arrived()  # at 5 s, audio not yet playing
    assert turn.next_at() is None
    assert all(turn.due(t) is None for t in (8.0, 15.0, 20.0, 60.0))


def test_a_lookup_ack_that_follows_the_servers_own_sentence_plays_no_filler_line() -> None:
    turn = TurnFillers(Fillers.load())
    assert turn.tool("web_answer", said=True) is None
    assert turn.say_pending and not turn.filled
