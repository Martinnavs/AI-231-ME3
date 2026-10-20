"""PauseDetector: speech and pauses relative to the room's noise floor."""

from __future__ import annotations

from lan_speech.pause import NoiseFloor, PauseDetector


def pauses(det: PauseDetector, levels: list[float]) -> list[int]:
    return [i for i, rms in enumerate(levels) if det.feed(rms)[1]]


def test_a_noise_hint_skips_the_calibration_second() -> None:
    det = PauseDetector(0.2, noise=0.005)
    voiced, _ = det.feed(0.1)
    assert voiced  # without the hint the first second is only measured


def test_without_a_hint_the_first_second_is_only_measured() -> None:
    det = PauseDetector(0.2)
    assert [det.feed(0.5)[0] for _ in range(10)] == [False] * 10


def test_a_short_pause_is_reported_once_per_stretch_of_speech() -> None:
    det = PauseDetector(0.2, noise=0.005)
    levels = [0.1] * 5 + [0.005] * 6 + [0.1] * 5 + [0.005] * 6
    assert pauses(det, levels) == [6, 17]  # two quiet chunks (0.2 s) after each stretch


def test_the_noise_floor_is_measured_from_quiet_chunks_only() -> None:
    det = PauseDetector(0.2, noise=0.005)
    for rms in [0.1] * 30 + [0.01] * 25:
        det.feed(rms)
    assert det.measured and abs(det.noise - 0.01) < 1e-9


def test_the_noise_floor_can_be_shared_between_turns() -> None:
    shared = NoiseFloor()
    assert shared.value is None
    shared.value = 0.02
    det = PauseDetector(0.2, noise=shared.value)
    assert det.threshold == max(det.floor, det.ratio * 0.02)


def test_a_soft_tail_just_above_the_noise_counts_as_resumed_speech() -> None:
    det = PauseDetector(0.2, noise=0.01)  # speech bar 0.025, resume bar 0.015
    assert not det.above_floor(0.012)  # the room
    assert det.above_floor(0.02)  # too soft to be "speech", too loud to be the room
    assert not det.feed(0.02)[0]
