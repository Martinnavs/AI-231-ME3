"""A cautious echo bar for the first second of each reply (.scratch/echo-next/PLAN.md, E2).

Live (canceller only), the guard learned its coupling from cancelled audio (about 0.1-0.3) and
fired on the canceller's leak in a reply's first second: mic level about 0.06 at a played level
of about 0.11.
"""

import numpy as np

from doc_chat.bargein import BargeInGuard, EchoModel

SR = 16000


def _block(level: float, seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    return (rng.normal(0, level, SR // 10) * 32767).astype(np.int16).tobytes()


def _learned_model() -> EchoModel:
    m = EchoModel(prior=0.8, margin=1.2, window=60, quantile=0.95)
    for _ in range(20):
        m.learn(0.01, 0.1)  # it has learned a coupling of 0.1 (cancelled audio)
    return m


def _fires(warmup_blocks: int, n: int) -> list[bool]:
    g = BargeInGuard(
        0.005, mode="energy", echo=_learned_model(), level_kofn=(3, 8), warmup_blocks=warmup_blocks
    )
    return [g.feed(_block(0.06, k), played=0.11) for k in range(n)]


def test_without_warmup_a_leak_at_the_start_of_a_reply_fires() -> None:
    assert any(_fires(warmup_blocks=0, n=10))


def test_with_warmup_the_same_leak_does_not_fire_in_the_first_second() -> None:
    assert not any(_fires(warmup_blocks=10, n=10))


def test_after_the_warmup_the_learned_bar_applies_again() -> None:
    fired = _fires(warmup_blocks=10, n=14)
    assert not any(fired[:10]) and any(fired[10:])


def test_a_voice_well_above_the_prior_still_fires_during_the_warmup() -> None:
    g = BargeInGuard(
        0.005, mode="energy", echo=_learned_model(), level_kofn=(3, 8), warmup_blocks=10
    )
    assert any(g.feed(_block(0.3, k), played=0.11) for k in range(5))


def test_the_warmup_starts_again_with_each_reply() -> None:
    g = BargeInGuard(
        0.005, mode="energy", echo=_learned_model(), level_kofn=(3, 8), warmup_blocks=10
    )
    for k in range(12):
        g.feed(_block(0.001, k), played=0.11)
    g.reset()
    assert not any(g.feed(_block(0.06, k), played=0.11) for k in range(10))


def test_the_warmup_does_not_change_what_the_model_learns() -> None:
    m = _learned_model()
    g = BargeInGuard(0.005, mode="energy", echo=m, level_kofn=(3, 8), warmup_blocks=10)
    before = m.coupling
    for k in range(10):
        g.feed(_block(0.06, k), played=0.11)  # above the learned bar: not learned from
    assert m.coupling == before


def _short_word(g: BargeInGuard) -> list[bool]:
    quiet, loud = _block(0.001, 1), _block(0.3, 2)
    return [g.feed(b, played=0.05) for b in (quiet, loud, loud, quiet, quiet)]


def test_two_loud_blocks_do_not_fire_with_three_of_eight() -> None:
    g = BargeInGuard(0.005, mode="energy", echo=_learned_model(), level_kofn=(3, 8), ducked_k=2)
    assert not any(_short_word(g))  # not ducked: the usual 3 of 8


def test_while_ducked_two_loud_blocks_are_enough() -> None:
    g = BargeInGuard(0.005, mode="energy", echo=_learned_model(), level_kofn=(3, 8), ducked_k=2)
    g.ducked = True
    assert any(_short_word(g))
