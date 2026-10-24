"""'Pause' is an order in the pivot strip, not a stop word (.scratch/echo-next/PLAN.md, E3)."""

import pytest

pytest.importorskip("langchain_core")

from doc_chat.__main__ import _is_stop_phrase, _strip_stop_prefix  # noqa: E402


def test_stop_then_pause_keeps_the_order_to_pause() -> None:
    assert _strip_stop_prefix("Stop. Pause the music.") == "Pause the music."
    assert _strip_stop_prefix("Stop, pause the music.") == "Pause the music."
    assert _strip_stop_prefix("Wait, pause the video please") == "Pause the video please"


def test_pivots_without_pause_are_unchanged() -> None:
    assert _strip_stop_prefix("Wait, stop. Tell me about Suttree instead.") == (
        "Tell me about Suttree instead."
    )
    assert _strip_stop_prefix("Stop. Play some music.") == "Play some music."
    assert _strip_stop_prefix("Pause the music.") == "Pause the music."


def test_a_bare_pause_still_only_silences() -> None:
    assert _is_stop_phrase(["pause"])
    assert _is_stop_phrase(["wait", "pause"], after_interrupt=True)
