# Ticket 01: "pause" is an order, not a stop word, in the pivot strip

**Status:** delivered-for-review
**Date:** 2026-10-24
**Blocked by:** None

Today `_strip_stop_prefix("Stop. Pause the music.")` returns "The music." (and "Wait, pause the video please" returns "The video please"): "pause" is in the stop-word prefix, so the order to pause is cut off with the stop. Found in `.scratch/echo-next/PLAN.md` E3. A bare "pause" still only silences the assistant (`_is_stop_phrase`, unchanged).

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `src/doc_chat/__main__.py` | edit (list below) | **1326** (now 1325; exactly 1326 after the edits) |
| `tests/doc_chat/test_stop_pause.py` | new, verbatim | **26** |

**Non-goals / touch nothing else:** no change to `_is_stop_phrase`, `_STOP_WORDS`, `_STOP_CORE` or any other rule; no other file.

**Security-sensitive:** no (local text and audio logic; no secrets, no network).

## Edits

Apply in this order. Each OLD block occurs exactly once in the file as it is at that moment; replace it with NEW, byte-for-byte.

### Edit 1: `src/doc_chat/__main__.py`

OLD:

````python
_STOP_PREFIX = re.compile(
    r"^\W*(?:(?:wait|stop|hold on|hang on|pause|okay|ok|please|sorry|actually|hey|um|uh|so)\b\W*)+",
    re.IGNORECASE,
)
````

NEW:

````python
_STOP_PREFIX = re.compile(
    r"^\W*(?:(?:wait|stop|hold on|hang on|okay|ok|please|sorry|actually|hey|um|uh|so)\b\W*)+",
    re.IGNORECASE,
)
````

### Edit 2: `src/doc_chat/__main__.py`

OLD:

````python
    question would search the documents and prompt the model with "wait stop" as well. Only a
    prefix that contains a real stop word is removed, and only if a question remains."""
````

NEW:

````python
    question would search the documents and prompt the model with "wait stop" as well. Only a
    prefix that contains a real stop word is removed, and only if a question remains. "Pause" is
    not one here: "Stop. Pause the music." is an order to pause the music."""
````

### Edit 3: `src/doc_chat/__main__.py`

OLD:

````python
    has_stop = re.search(r"\b(wait|stop|hold on|hang on|pause)\b", prefix, re.IGNORECASE)
    punctuated = re.search(r"\b(wait|stop|pause|on)\b\s*[,.!?;:-]", prefix, re.IGNORECASE)
````

NEW:

````python
    has_stop = re.search(r"\b(wait|stop|hold on|hang on)\b", prefix, re.IGNORECASE)
    punctuated = re.search(r"\b(wait|stop|on)\b\s*[,.!?;:-]", prefix, re.IGNORECASE)
````

## New test file(s), verbatim

### `tests/doc_chat/test_stop_pause.py`

````python
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
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `uv run --offline --group laptop ruff check src/doc_chat/__main__.py tests/doc_chat/test_stop_pause.py` | All checks passed! |
| 2 | `uv run --offline --group laptop ruff format --check src/doc_chat/__main__.py tests/doc_chat/test_stop_pause.py` | 2 files already formatted |
| 3 | `uv run --offline --group laptop mypy` | Success: no issues found in 54 source files |
| 4 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_stop_pause.py` | 3 passed |
| 5 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_bargein.py` | all passed, 0 failed (count printed) |

## Deviations

none

## Execution Log

### 2026-10-24 agy: DONE
- Proof: 1 ✅ `uv run --offline --group laptop ruff check src/doc_chat/__main__.py tests/doc_chat/test_stop_pause.py` (All checks passed!) 2 ✅ `uv run --offline --group laptop ruff format --check src/doc_chat/__main__.py tests/doc_chat/test_stop_pause.py` (2 files already formatted) 3 ✅ `uv run --offline --group laptop mypy` (Success: no issues found in 54 source files) 4 ✅ `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_stop_pause.py` (3 passed in 0.80s) 5 ✅ `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_bargein.py` (46 passed in 1.98s)
- Files: src/doc_chat/__main__.py (1326 / 1326)
- Files: tests/doc_chat/test_stop_pause.py (26 / 26)
- Deviations: none
- Notes for reviewer: none



### 2026-10-24 reviewer (assistant): ACCEPTED
- `diff -rq orig work`: only files in the four tickets' tables (and the tickets) changed.
- `cmp` against a freshly built reference (`build_overlay.sh --no-gate`): every file byte-identical.
- Integrated into the repository (backup `.scratch/echo-next/backup-125013/`); repository gate: ruff and mypy clean, pytest 616 passed, 9 skipped, 2 xfailed, 1 failed (the known `test_flow::test_llm_echo_is_accepted_without_documents`).
