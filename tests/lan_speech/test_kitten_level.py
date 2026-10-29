import numpy as np

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
    assert kitten_text("It's 6:25 AM.", lambda t: t, lambda n: str(n)).endswith("ay em.")
