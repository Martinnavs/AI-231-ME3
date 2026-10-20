"""Phonetic entity repair and the repair ladder."""

from __future__ import annotations

import pytest

from lan_speech.transcript_repair import (
    Entity,
    EntityMatcher,
    RepairConfig,
    Risk,
    Rung,
    Word,
    build_matchers,
    find_matches,
    infer_context,
    phonemes,
    phonetic_distance,
    repair,
    repair_text,
    repair_to_dict,
)

NAMES = [
    Entity("Opalite", "song"),
    Entity("Taylor Swift", "artist"),
    Entity("BINI", "artist"),
    Entity("Pantropiko", "song"),
    Entity("Ysabel Dela Cruz", "contact"),
    Entity("Angelo", "contact"),
    Entity("Kristine", "contact"),
    Entity("Christopher", "contact"),
    Entity("Makati", "place"),
    Entity("Quezon City", "place"),
]


@pytest.fixture
def matcher() -> EntityMatcher:
    return EntityMatcher(NAMES)


def words(text: str, conf: float | None = None) -> list[Word]:
    return [Word(w, conf) for w in text.split()]


def dist(a: str, b: str) -> float:
    return phonetic_distance(phonemes(a), phonemes(b))


def test_near_misses_are_close_and_unrelated_words_are_far() -> None:
    assert dist("opalate", "Opalite") < 0.1
    assert dist("Pantropico", "Pantropiko") < 0.05
    assert dist("Binnie", "BINI") < 0.15
    assert dist("Christine", "Kristine") < 0.15
    assert dist("play", "BINI") > 0.5
    assert dist("weather", "Makati") > 0.5


def test_digraphs_and_doubles_fold_to_the_same_sound() -> None:
    assert phonemes("Christine") == phonemes("Kristine")
    assert phonemes("Clarisse") == phonemes("Clarice")
    assert phonemes("phone") == phonemes("fone")


def test_a_clear_close_match_is_replaced_silently(matcher: EntityMatcher) -> None:
    r = repair(words("play opalate by taylor swift"), matcher)
    assert r.text == "play Opalite by taylor swift"  # a listed name is left as heard
    assert r.rung is Rung.SILENT
    assert r.question is None


def test_a_listed_name_is_left_alone(matcher: EntityMatcher) -> None:
    r = repair(words("call Angelo"), matcher)
    assert r.text == "call Angelo"
    assert r.rung is Rung.NONE
    assert r.matches == ()


def test_ordinary_words_are_never_turned_into_names(matcher: EntityMatcher) -> None:
    r = repair(words("what is the weather in the morning"), matcher)
    assert r.rung is Rung.NONE
    assert r.text == "what is the weather in the morning"


def test_a_multi_word_span_maps_to_a_multi_word_name(matcher: EntityMatcher) -> None:
    r = repair(words("call paul isabelle de la cruz"), matcher, risk=Risk.LOW)
    assert any(m.entity.name == "Ysabel Dela Cruz" for m in r.matches)


def test_a_middling_match_is_echoed_for_low_risk_actions(matcher: EntityMatcher) -> None:
    cfg = RepairConfig(silent_max=0.05, implicit_max=0.30)
    r = repair(words("play panthropical"), matcher, cfg=cfg)
    assert r.rung is Rung.IMPLICIT
    assert r.echo == ("Pantropiko",)
    assert "Pantropiko" in r.text


def test_high_risk_actions_ask_instead_of_assuming(matcher: EntityMatcher) -> None:
    cfg = RepairConfig(silent_max=0.16, high_risk_silent_max=0.02, implicit_max=0.26)
    low = repair(words("call angeilo"), matcher, risk=Risk.LOW, cfg=cfg)
    high = repair(words("call angeilo"), matcher, risk=Risk.HIGH, cfg=cfg)
    assert low.rung is Rung.SILENT
    assert low.text == "call Angelo"
    assert high.rung is Rung.CONFIRM
    assert high.question == "Did you mean Angelo?"
    assert high.text == "call angeilo"  # not replaced until the user confirms


def test_names_that_sound_identical_are_replaced_even_for_risky_actions(
    matcher: EntityMatcher,
) -> None:
    r = repair(words("call christine"), matcher, risk=Risk.HIGH)
    assert r.rung is Rung.SILENT and r.text == "call Kristine"


def test_two_equally_close_names_are_offered_together() -> None:
    m = EntityMatcher([Entity("Kristine", "contact"), Entity("Christine", "contact")])
    r = repair(words("text kristene"), m)
    assert r.rung is Rung.CONFIRM
    assert r.question is not None and "Kristine" in r.question and "Christine" in r.question


def test_a_missing_expected_name_asks_for_just_that_slot(matcher: EntityMatcher) -> None:
    r = repair(words("send a message saying hello"), matcher, expected_category="contact")
    assert r.rung is Rung.TARGETED
    assert r.question == "Which contact was that?"


def test_a_confident_expected_name_needs_no_question(matcher: EntityMatcher) -> None:
    r = repair(words("text Angelo hello"), matcher, expected_category="contact")
    assert r.rung is Rung.NONE


def test_empty_or_unintelligible_turns_are_reprompted(matcher: EntityMatcher) -> None:
    assert repair([], matcher).rung is Rung.REPROMPT
    assert repair(words("mumble mumble", conf=0.1), matcher).rung is Rung.REPROMPT


def test_words_the_decoder_is_sure_of_are_not_touched(matcher: EntityMatcher) -> None:
    sure = [Word("play", 0.99), Word("opalate", 0.99)]
    assert find_matches(sure, matcher) == []
    unsure = [Word("play", 0.99), Word("opalate", 0.6)]
    assert [m.entity.name for m in find_matches(unsure, matcher)] == ["Opalite"]


def test_matches_do_not_overlap_and_keep_position_order(matcher: EntityMatcher) -> None:
    ms = find_matches(words("play opalate by taylor swyft near makaty"), matcher)
    spans = [(m.start, m.end) for m in ms]
    assert spans == sorted(spans)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))


def test_a_leading_ordinary_word_must_sound_like_the_name(matcher: EntityMatcher) -> None:
    m = EntityMatcher([Entity("Katrina", "contact")])
    ms = find_matches(words("call rowana"), m, Risk.HIGH)
    assert all("call" not in match.heard for match in ms)


def test_names_that_begin_with_an_ordinary_word_still_match() -> None:
    m = EntityMatcher([Entity("The Weeknd", "artist")])
    ms = find_matches(words("play the weekned"), m)
    assert [match.entity.name for match in ms] == ["The Weeknd"]
    assert ms[0].heard == "the weekned"


@pytest.mark.parametrize(
    ("text", "slot", "risk"),
    [
        ("Call Jocelyn", "contact", Risk.HIGH),
        ("called Jocelyn", "contact", Risk.HIGH),
        ("Can you please text Angelo", "contact", Risk.HIGH),
        ("playing Opalite", "music", Risk.LOW),
        ("Put on some BINI", "music", Risk.LOW),
        ("What is the weather in Cebu", "place", Risk.LOW),
        ("Book me a Grab to Makati", "brand+place", Risk.LOW),
        ("Call me Ishmael", None, Risk.LOW),
        ("Text me when you get there", None, Risk.LOW),
        ("Send it to the printer", None, Risk.LOW),
        ("Who won the Nobel prize", None, Risk.LOW),
        ("", None, Risk.LOW),
    ],
)
def test_the_slot_and_risk_are_guessed_from_the_first_verb(
    text: str, slot: str | None, risk: Risk
) -> None:
    assert infer_context(text) == (slot, risk)


def test_a_combined_slot_searches_every_listed_kind() -> None:
    matchers = build_matchers([Entity("Grab", "brand"), Entity("Makati", "place")])
    r = repair_text("book a grap to makaty", matchers, "brand+place", Risk.LOW)
    assert r is not None
    assert {m.entity.name for m in r.matches} == {"Grab", "Makati"}
    assert r.question is None


def test_no_list_for_the_slot_means_no_repair() -> None:
    matchers = build_matchers([Entity("Grab", "brand")])
    assert repair_text("call jocelyn", matchers, "contact", Risk.HIGH) is None
    assert repair_text("call jocelyn", matchers, None, Risk.LOW) is None


def test_the_slot_question_uses_a_natural_noun(matcher: EntityMatcher) -> None:
    r = repair(words("play something"), matcher, expected_category="song")
    assert r.question == "Which song was that?"
    m = EntityMatcher([Entity("Opalite", "music")])
    assert repair(words("play it"), m, expected_category="music").question == (
        "Which artist or song was that?"
    )


def test_the_repair_summary_for_the_protocol_is_json_friendly() -> None:
    matchers = build_matchers([Entity("Opalite", "music")])
    r = repair_text("play opalate", matchers, "music", Risk.LOW)
    assert r is not None
    d = repair_to_dict(r, "music", Risk.LOW)
    assert d["rung"] == "silent" and d["slot"] == "music" and d["risk"] == "low"
    assert d["matches"][0]["entity"] == "Opalite"  # type: ignore[index]
    import json

    json.dumps(d)
