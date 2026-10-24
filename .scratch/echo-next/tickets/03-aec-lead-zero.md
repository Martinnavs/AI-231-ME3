# Ticket 03: no reference lead by default in the echo canceller

**Status:** delivered-for-review
**Date:** 2026-10-24
**Blocked by:** ticket 02 (edits the same `__main__.py`; run in order)

WebRTC AEC3 resets its render buffer when the reference stays more than 32 ms ahead of the microphone for a second, and finds render-to-echo delays only up to about 0.5 s. The 50 ms lead triggers the reset and uses up range; offline, lead 0 left the least echo 1-2 s into a clip (`.scratch/echo-next/PLAN.md` E1). The default goes to 0; `--aec-lead-ms` still sets a lead.

Read `GUARDRAILS.md` (this folder) first; it binds this ticket.

## Files

| File | Action | Line cap |
|---|---|---|
| `src/doc_chat/aec.py` | edit (list below) | **86** (now 84; exactly 86 after the edits) |
| `src/doc_chat/__main__.py` | edit (list below) | **1344** (now 1344; exactly 1344 after the edits) |
| `tests/doc_chat/test_aec_lead.py` | new, verbatim | **25** |

**Non-goals / touch nothing else:** no change to `set_stream_delay_ms`, the frame logic or `RemotePlayer`; no other file.

**Security-sensitive:** no (local text and audio logic; no secrets, no network).

## Edits

Apply in this order. Each OLD block occurs exactly once in the file as it is at that moment; replace it with NEW, byte-for-byte.

### Edit 1: `src/doc_chat/aec.py`

OLD:

````python
Both streams are fed in 10 ms frames in lockstep, as the canceller expects. The first slice of a
reply starts `lead_ms` of reference ahead. 50 ms was the best of 0-300 ms on a real recording
(docs/AEC-REMOTE-SPEAKER.md): more makes the canceller worse as the path delay grows.
"""
````

NEW:

````python
Both streams are fed in 10 ms frames in lockstep, as the canceller expects. The first slice of a
reply can start `lead_ms` of reference ahead; the default is none. AEC3 resets its render buffer
when the reference stays more than 32 ms ahead of the microphone for a second, and it finds
render-to-echo delays only up to about 0.5 s, so any lead uses up that range
(.scratch/echo-next/PLAN.md, E1: lead 0 left the least echo 1-2 s into a clip).
"""
````

### Edit 2: `src/doc_chat/aec.py`

OLD:

````python
    def __init__(self, lead_ms: int = 50) -> None:
````

NEW:

````python
    def __init__(self, lead_ms: int = 0) -> None:
````

### Edit 3: `src/doc_chat/__main__.py`

OLD:

````python
        "--aec-lead-ms",
        type=int,
        default=50,
        help="with --aec: ms of reference fed ahead of the microphone (default: 50; more is worse "
        "as the path delay grows)",
````

NEW:

````python
        "--aec-lead-ms",
        type=int,
        default=0,
        help="with --aec: ms of reference fed ahead of the microphone (default: 0; any lead uses "
        "up the canceller's ~0.5 s delay range)",
````

## New test file(s), verbatim

### `tests/doc_chat/test_aec_lead.py`

````python
"""The canceller feeds no reference ahead by default (.scratch/echo-next/PLAN.md, E1)."""

from doc_chat.aec import FRAME_BYTES, EchoCanceller


def test_no_lead_by_default() -> None:
    assert EchoCanceller()._lead == 0


def test_a_reply_start_renders_one_frame_per_microphone_frame() -> None:
    canceller = EchoCanceller()
    rendered: list[bytes] = []
    canceller._render = rendered.append  # type: ignore[method-assign]
    canceller.reference(bytes(FRAME_BYTES * 10))
    canceller.process(bytes(FRAME_BYTES * 3))
    assert len(rendered) == 3 and len(canceller._frames) == 7


def test_an_explicit_lead_is_still_fed_ahead() -> None:
    canceller = EchoCanceller(lead_ms=50)
    rendered: list[bytes] = []
    canceller._render = rendered.append  # type: ignore[method-assign]
    canceller.reference(bytes(FRAME_BYTES * 10))
    canceller.process(bytes(FRAME_BYTES))
    assert len(rendered) == 6  # 5 frames of lead, then the frame for this microphone frame
````

## Proof

Run from the repository root, in order. Expected last line in the right column.

| # | Command | Expected |
|---|---|---|
| 1 | `uv run --offline --group laptop ruff check src/doc_chat/aec.py src/doc_chat/__main__.py tests/doc_chat/test_aec_lead.py` | All checks passed! |
| 2 | `uv run --offline --group laptop ruff format --check src/doc_chat/aec.py src/doc_chat/__main__.py tests/doc_chat/test_aec_lead.py` | 3 files already formatted |
| 3 | `uv run --offline --group laptop mypy` | Success: no issues found in 54 source files |
| 4 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_aec_lead.py` | 3 passed |
| 5 | `uv run --offline --group laptop pytest -q -p no:cacheprovider tests/doc_chat/test_aec.py` | all passed, 0 failed (count printed) |

## Deviations

(none yet)

## Execution Log

### 2026-10-24 reviewer (assistant): DONE (agy landed the code, then its connection dropped)
- agy's wave 1 ended with a network error ("proto: unexpected EOF") after writing this ticket's files and before running its Proof or logging. The files are byte-identical to the reference (state after ticket 03).
- Proof, run by the reviewer: 1 ✅ All checks passed! 2 ✅ 3 files already formatted 3 ✅ Success: no issues found in 54 source files 4 ✅ 3 passed 5 ✅ 6 passed
- Files: src/doc_chat/aec.py (86 / 86); src/doc_chat/__main__.py (1344 / 1344); tests/doc_chat/test_aec_lead.py (25 / 25)
- Deviations: none

### 2026-10-24 reviewer (assistant): ACCEPTED
- `diff -rq orig work`: only files in the four tickets' tables (and the tickets) changed.
- `cmp` against a freshly built reference (`build_overlay.sh --no-gate`): every file byte-identical.
- Integrated into the repository (backup `.scratch/echo-next/backup-125013/`); repository gate: ruff and mypy clean, pytest 616 passed, 9 skipped, 2 xfailed, 1 failed (the known `test_flow::test_llm_echo_is_accepted_without_documents`).
