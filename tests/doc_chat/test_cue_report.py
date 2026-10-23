"""scripts/cue_report.py: what counts as a hit, a miss, and a false trigger."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cue_report.py"
spec = importlib.util.spec_from_file_location("cue_report", SCRIPT)
assert spec and spec.loader
cue_report = importlib.util.module_from_spec(spec)
sys.modules["cue_report"] = cue_report
spec.loader.exec_module(cue_report)


def _reply(cue: float | None) -> dict[str, object]:
    return {"answer_words": 20, "llm_error": None, "cue_s": cue}


def _cut(cue: float | None, after_ms: float) -> dict[str, object]:
    return {"interrupted": "barge_in", "cue_s": cue, "interrupt_after_answer_start_ms": after_ms}


def test_classify_separates_hits_misses_and_false_triggers() -> None:
    rows = [
        _cut(2.5, 3300.0),  # hit, 0.8 s after the beep
        _cut(2.5, 1800.0),  # before the beep: the assistant heard itself
        _reply(2.5),  # beep played, nobody talked: missed
        _reply(None),  # quiet reply that stayed quiet
        _cut(None, 6000.0),  # a quiet reply was interrupted: false trigger
        {"ignored_echo": True},
        {"stop_word": True},
    ]
    c = cue_report.classify(rows)
    assert [round(h, 1) for h in c["hits"]] == [0.8]
    assert (c["missed"], c["quiet_ok"], c["false_quiet"], c["false_early"]) == (1, 1, 1, 1)
    assert (c["ignored_echo"], c["stop_words"]) == (1, 1)


def test_a_failed_reply_is_not_counted_as_a_quiet_success() -> None:
    c = cue_report.classify([{"answer_words": 0, "llm_error": "timeout", "cue_s": None}])
    assert c["quiet_ok"] == 0
