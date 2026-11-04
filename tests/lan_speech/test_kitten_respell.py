import pytest

from lan_speech.engines import KITTEN_RESPELLINGS, kitten_text
from lan_speech.hotwords import hotword_names, load_entities

WORDS = {29004: "twenty-nine thousand and four", 29032: "twenty-nine thousand and thirty-two"}
WORDS.update({29100: "twenty-nine thousand one hundred", 29000: "twenty-nine thousand"})


def say(text: str) -> str:
    return kitten_text(text, lambda t: t, lambda n: str(n), lambda n: WORDS[n], lambda n: str(n))


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("EDSA is closed.", "Ed sah is closed."),
        ("Take edsa to Makati.", "Take Ed sah to Makati."),
        ("EDSA's traffic.", "Ed sah's traffic."),
        ("President Duterte spoke.", "President Du-tair-tay spoke."),
        ("The EDSAS bus.", "The EDSAS bus."),
    ],
)
def test_respelling_applies_to_whole_words_only(text: str, said: str) -> None:
    assert say(text) == said


def test_every_respelling_is_a_plain_string_that_does_not_trigger_itself() -> None:
    for word, spelling in KITTEN_RESPELLINGS.items():
        assert word not in spelling
        assert say(spelling) == spelling


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("Count 29,004.", "Count twenty-nine thousand and four."),
        ("Count 29,032.", "Count twenty-nine thousand and thirty-two."),
        ("Count 29,100.", "Count twenty-nine thousand, one hundred."),
        ("Count 29,000.", "Count twenty-nine thousand."),
    ],
)
def test_one_or_two_digit_tail_after_thousands_uses_and(text: str, said: str) -> None:
    assert say(text) == said


def test_hotword_names_skips_repair_only_categories(tmp_path) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "e.json"
    f.write_text('{"Suttree": "doc", "EDSA": "term", "Cebu": "place"}')
    ents = load_entities(f)
    assert hotword_names(ents) == ["Suttree", "EDSA", "Cebu"]
    assert hotword_names(ents, frozenset({"doc"})) == ["EDSA", "Cebu"]
