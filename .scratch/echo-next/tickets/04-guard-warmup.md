# Ticket 04: a cautious echo bar for the first second of each reply (--barge-warmup-s)

**Status:** delivered-for-review
**Date:** 2026-10-24
**Blocked by:** ticket 03 (edits the same `__main__.py`; run in order)

With the canceller, the guard learns its coupling from cancelled audio, so its bar sits near the room floor, and the canceller leaks while it settles at the start of a reply: 7 of 20 replies cut themselves off live. Replayed from the trace, using the echo model's prior for the first 1.0 s (bar >= margin x prior x played) removed all 7 and kept every cued interruption (`.scratch/echo-next/PLAN.md` E2). Behind a flag, off by default; the model still learns against the ordinary bar.

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `src/doc_chat/bargein.py` | edit (list below) | **438** (now 425; exactly 438 after the edits) |
| `src/doc_chat/__main__.py` | edit (list below) | **1353** (now 1344; exactly 1353 after the edits) |
| `tests/doc_chat/test_guard_warmup.py` | new, verbatim | **70** |

**Non-goals / touch nothing else:** no change to `EchoModel`, `OnsetDetector`, the Silero stage or the duck; nothing changes without `--barge-warmup-s`; no other file.

**Security-sensitive:** no (local text and audio logic; no secrets, no network).

## Edits

Apply in this order. Each OLD block occurs exactly once in the file as it is at that moment; replace it with NEW, byte-for-byte.

### Edit 1: `src/doc_chat/bargein.py`

OLD:

````python
        echo: EchoModel | None = None,
        level_kofn: tuple[int, int] | None = None,
    ) -> None:
        self.mode = mode
````

NEW:

````python
        echo: EchoModel | None = None,
        level_kofn: tuple[int, int] | None = None,
        warmup_blocks: int = 0,
    ) -> None:
        self.mode = mode
        # for the first warmup_blocks blocks of a reply the echo allowance uses the echo model's
        # prior (no canceller), not what it learned from cancelled audio: the canceller leaks
        # while it settles at the start of a reply (.scratch/echo-next/PLAN.md, E2)
        self.warmup_blocks = warmup_blocks
        self._played_blocks = 0
````

### Edit 2: `src/doc_chat/bargein.py`

OLD:

````python
        self._above.clear()
        if self.silero is not None:
            self.silero.reset()
````

NEW:

````python
        self._above.clear()
        self._played_blocks = 0
        if self.silero is not None:
            self.silero.reset()
````

### Edit 3: `src/doc_chat/bargein.py`

OLD:

````python
        extra = self.echo.expected(played) if use_echo and self.echo and played > 0.01 else 0.0
        fired = self.playing.feed(level, dur, extra)
        bar = max(self.playing.threshold, extra)
        if self.level_kofn is not None:
            self._above.append(level > bar)
            fired = level > bar and sum(self._above) >= self.level_kofn[0]
        self.last_bar, self.last_level = bar, level
````

NEW:

````python
        extra = self.echo.expected(played) if use_echo and self.echo and played > 0.01 else 0.0
        if self._played_blocks or played > 0:
            self._played_blocks += 1
        guarded = extra
        if self.echo is not None and 0 < self._played_blocks <= self.warmup_blocks:
            guarded = max(extra, self.echo.margin * self.echo.prior * played)
        fired = self.playing.feed(level, dur, guarded)
        bar = max(self.playing.threshold, extra)
        guarded_bar = max(self.playing.threshold, guarded)
        if self.level_kofn is not None:
            self._above.append(level > guarded_bar)
            fired = level > guarded_bar and sum(self._above) >= self.level_kofn[0]
        self.last_bar, self.last_level = guarded_bar, level
````

### Edit 4: `src/doc_chat/__main__.py`

OLD:

````python
            level_kofn=_kofn(getattr(self.args, "level_kofn", None)),
        )

        trace = self._guard_trace
````

NEW:

````python
            level_kofn=_kofn(getattr(self.args, "level_kofn", None)),
            warmup_blocks=round(float(getattr(self.args, "barge_warmup_s", 0.0)) * 10),
        )

        trace = self._guard_trace
````

### Edit 5: `src/doc_chat/__main__.py`

OLD:

````python
    parser.add_argument(
        "--barge-echo-prior",
````

NEW:

````python
    parser.add_argument(
        "--barge-warmup-s",
        type=float,
        default=0.0,
        help="for this many seconds at the start of each reply, expect the echo of the prior "
        "coupling (--barge-echo-prior) even if less was learned: the canceller leaks while it "
        "settles (try 1.0 with --aec; .scratch/echo-next/PLAN.md, E2)",
    )
    parser.add_argument(
        "--barge-echo-prior",
````

## New test file(s), verbatim

### `tests/doc_chat/test_guard_warmup.py`

````python
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
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `uv run --offline --group laptop ruff check src/doc_chat/bargein.py src/doc_chat/__main__.py tests/doc_chat/test_guard_warmup.py` | All checks passed! |
| 2 | `uv run --offline --group laptop ruff format --check src/doc_chat/bargein.py src/doc_chat/__main__.py tests/doc_chat/test_guard_warmup.py` | 3 files already formatted |
| 3 | `uv run --offline --group laptop mypy` | Success: no issues found in 54 source files |
| 4 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_guard_warmup.py` | 6 passed |
| 5 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_bargein.py tests/doc_chat/test_duck.py` | all passed, 0 failed (count printed) |
| 6 | `uv run --offline --group laptop pytest -q -p no:cacheprovider` | exactly 1 failed: tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents (needs a speech server; known, fails before these tickets too); everything else passes or skips |

## Deviations

none

## Execution Log

### 2026-10-24 agy: DONE
- Proof: 1 ✅ `uv run --offline --group laptop ruff check src/doc_chat/bargein.py src/doc_chat/__main__.py tests/doc_chat/test_guard_warmup.py` (All checks passed!) 2 ✅ `uv run --offline --group laptop ruff format --check src/doc_chat/bargein.py src/doc_chat/__main__.py tests/doc_chat/test_guard_warmup.py` (3 files already formatted) 3 ✅ `uv run --offline --group laptop mypy` (Success: no issues found in 54 source files) 4 ✅ `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_guard_warmup.py` (6 passed in 0.12s) 5 ✅ `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_bargein.py tests/doc_chat/test_duck.py` (53 passed in 2.82s) 6 ✅ `uv run --offline --group laptop pytest -q -p no:cacheprovider` (1 failed, 612 passed, 13 skipped, 2 xfailed, 2 warnings in 89.66s (0:01:29))
- Files: src/doc_chat/bargein.py (438 / 438)
- Files: src/doc_chat/__main__.py (1353 / 1353)
- Files: tests/doc_chat/test_guard_warmup.py (70 / 70)
- Deviations: none
- Notes for reviewer: Proof 6 suite run ended with exactly 1 failure as expected (tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents, known failure needing live speech server).

### 2026-10-24 reviewer (assistant): ACCEPTED
- `diff -rq orig work`: only files in the four tickets' tables (and the tickets) changed.
- `cmp` against a freshly built reference (`build_overlay.sh --no-gate`): every file byte-identical.
- Integrated into the repository (backup `.scratch/echo-next/backup-125013/`); repository gate: ruff and mypy clean, pytest 616 passed, 9 skipped, 2 xfailed, 1 failed (the known `test_flow::test_llm_echo_is_accepted_without_documents`).
