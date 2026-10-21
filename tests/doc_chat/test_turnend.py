"""Text turn-end helpers, and the real model when it has been downloaded."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from doc_chat.turnend import (
    ADD_PROMPTS,
    DEFAULT_MODEL_DIR,
    MODEL_FILE,
    TextTurnModel,
    declines_to_add,
    pick_prompt,
    plain_text,
)


def test_plain_text_drops_punctuation_and_case_but_keeps_apostrophes() -> None:
    assert plain_text("Play Opalite, by Taylor Swift.") == "play opalite by taylor swift"
    assert plain_text("  What's  the  well-known  one?  ") == "what's the well-known one"


@pytest.mark.parametrize(
    "reply", ["no", "No.", "Nope", "That's all", "that's it, thanks", "Nothing else", "never mind"]
)
def test_these_replies_decline_to_add(reply: str) -> None:
    assert declines_to_add(reply)


@pytest.mark.parametrize(
    "reply", ["by Taylor Swift", "yes, the one from last year", "in the living room", ""]
)
def test_these_replies_continue_the_sentence(reply: str) -> None:
    assert not declines_to_add(reply)


def test_prompts_are_short_and_picked_from_the_list() -> None:
    assert all(len(p.split()) <= 8 for p in ADD_PROMPTS)
    assert pick_prompt(random.Random(1)) in ADD_PROMPTS


needs_model = pytest.mark.skipif(
    not (Path(DEFAULT_MODEL_DIR) / MODEL_FILE).exists(),
    reason="run python scripts/fetch_turn_model.py",
)


@needs_model
def test_the_model_tells_a_hanging_request_from_a_finished_one() -> None:
    model = TextTurnModel()
    assert model.prob("Play something by") < 0.3
    assert model.prob("I was thinking that we could go to the") < 0.3
    assert model.prob("What is the capital of France?") > 0.7
    assert model.looks_unfinished("play something by", 0.3)
    assert not model.looks_unfinished("", 0.3)  # nothing heard is not 'unfinished'


@needs_model
def test_a_full_stop_does_not_hide_an_unfinished_sentence() -> None:
    model = TextTurnModel()
    assert model.prob("Play something by.") == model.prob("play something by")
