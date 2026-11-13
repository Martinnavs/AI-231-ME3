import numpy as np
import pytest

from lan_speech.engines import KITTEN_TARGET_RMS_DBFS, kitten_text, match_level


def _tone(db: float, n: int = 24000) -> np.ndarray:
    """A sine whose RMS is `db` dBFS."""
    t = np.arange(n) / 24000
    return (np.sin(2 * np.pi * 220 * t) * 10 ** (db / 20) * np.sqrt(2)).astype(np.float32)


def _rms_db(x: np.ndarray) -> float:
    active = x[np.abs(x) > 0.01]
    return float(20 * np.log10(np.sqrt(np.mean(active**2))))


def test_louder_speech_is_brought_down_to_the_original_voices_level() -> None:
    out = match_level(_tone(-16.9))  # Kitten Micro's measured level
    assert abs(_rms_db(out) - KITTEN_TARGET_RMS_DBFS) < 0.1


def test_quieter_speech_is_raised_but_only_within_the_limits() -> None:
    assert abs(_rms_db(match_level(_tone(-23.0))) - KITTEN_TARGET_RMS_DBFS) < 0.1
    quiet = _tone(-34.0)
    gain = float(np.max(np.abs(match_level(quiet))) / np.max(np.abs(quiet)))
    assert abs(20 * np.log10(gain) - 3.0) < 0.01  # +3 dB at most: no pumping up of noise


def test_the_peak_is_limited_and_silence_is_left_alone() -> None:
    loud = _tone(-6.0)
    assert float(np.max(np.abs(match_level(loud, target_db=-3.0)))) <= 0.951
    silence = np.zeros(4000, dtype=np.float32)
    assert np.array_equal(match_level(silence), silence)


def test_chunks_of_different_loudness_end_up_the_same() -> None:
    a, b = match_level(_tone(-15.5)), match_level(_tone(-18.5))
    assert abs(_rms_db(a) - _rms_db(b)) < 0.1


def test_kitten_text_still_importable_here() -> None:
    assert kitten_text("It's 6:25 AM.", lambda t: t, lambda n: str(n)).endswith("A M.")


WORDS = {
    8849: "eight thousand, eight hundred and forty-nine",
    3932: "three thousand, nine hundred and thirty-two",
    250000: "two hundred and fifty thousand",
    1234567: "one million, two hundred and thirty-four thousand, five hundred and sixty-seven",
    1: "one",
    3: "three",
    4: "four",
    7: "seven",
}
ORDINALS = {4: "fourth", 13: "thirteenth", 1: "first"}


def say(text: str) -> str:
    return kitten_text(
        text, lambda t: t, lambda n: f"year{n}", lambda n: WORDS[n], lambda n: ORDINALS[n]
    )


def test_am_is_spelled_so_it_is_not_read_as_i_m() -> None:
    assert "ay em" not in say("It's 9:58 AM.")
    assert say("It's 9:58 AM.").endswith("A M.") and say("At 7 PM.").endswith("pee em.")


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("It is 8,848.86 meters.", "It is eight thousand, eight hundred forty-nine meters."),
        ("It is 8849 m high.", "It is eight thousand, eight hundred forty-nine m high."),
        ("Gold is 3,931.67 USD.", "Gold is three thousand, nine hundred thirty-two dollars."),
        ("Gold is $3,932.", "Gold is three thousand, nine hundred thirty-two dollars."),
        ("Up to 250,000 people.", "Up to two hundred fifty thousand people."),
        (
            "It has 1,234,567 votes.",
            "It has one million, two hundred thirty-four thousand, five hundred sixty-seven votes.",
        ),
    ],
)
def test_big_numbers_get_commas_and_lose_their_fraction(text: str, said: str) -> None:
    assert say(text) == said


def test_billions_are_said_as_a_decimal_of_billions() -> None:
    out = kitten_text(
        "It has 1,400,000,000 people.",
        lambda t: t,
        str,
        lambda n: {1: "one", 4: "four"}[n],
        lambda n: "",
    )
    assert out == "It has one point four billion people."


def test_small_numbers_times_and_decimals_are_left_to_the_cleaner() -> None:
    assert say("It is 1.7 gigabytes at 9:58.") == "It is 1.7 gigabytes at 9:58."
    assert say("74 percent of 21 people") == "74 percent of 21 people"


def test_date_with_an_ordinal_day_and_the_year_before_a_comma() -> None:
    assert say("On November 4, 2026, it rained.") == "On November fourth, year2026, it rained."
    assert say("Born in 1933, he left.") == "Born in year1933, he left."
    assert say("June 13, 2023") == "June thirteenth, year2023"


def test_a_score_or_range_reads_as_to() -> None:
    assert (
        say("A 3–3 draw, won 4–7.") == "A 3 to 3 draw, won 4 to 7."
    )  # the cleaner says the digits


def test_the_number_steps_are_skipped_without_the_helpers() -> None:
    text = "It is 8,848.86 m on June 13."
    assert kitten_text(text, lambda t: t, str) == text


def test_a_time_on_the_hour_is_not_read_as_three_hundred() -> None:
    """Kitten's time expander read "3:00 PM" as "three hundred pee em" (2026-11-13)."""
    assert say("It's 3:00 PM.") == "It's three pee em."
    assert say("Meet at 3:00.") == "Meet at three o'clock."
    assert say("It's 3:07 PM.").startswith("It's 3:07")  # other times are the cleaner's
