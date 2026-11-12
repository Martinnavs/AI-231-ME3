"""scripts/output_audit.py: the checks on what the listener hears, on small fixed logs."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

spec = importlib.util.spec_from_file_location(
    "output_audit", Path(__file__).resolve().parents[1] / "scripts" / "output_audit.py"
)
assert spec and spec.loader
oa = importlib.util.module_from_spec(spec)
sys.modules["output_audit"] = oa
spec.loader.exec_module(oa)


def row(time: str, question: str, text: str, *tools: str) -> dict[str, Any]:
    return {"time": time, "question": question, "text": text, "calls": [{"tool": t} for t in tools]}


def test_two_first_person_status_sentences_about_one_job_restate_each_other() -> None:
    said = (
        "I am updating the restaurant website to include the genre. "
        "OK, I am updating the website with those holidays. I will let you know."
    )
    assert oa.repeated(said)
    natural = "Looking up Marie Curie for you. Marie Curie was a physicist and chemist."
    assert not oa.repeated(natural)  # naming the topic again before the answer is natural
    assert not oa.repeated("I am checking the weather. It is 25 degrees in Cebu.")


def test_the_audit_counts_each_failure_once_per_turn() -> None:
    rows = [
        row(
            "10:00:00",
            "make a page",
            "I am building the restaurant website for you. I am building the website now, ready soon.",
            "delegate",
        ),
        row("10:00:10", "sorry I mean make a page", "Started.", "delegate"),  # inside 25 s
        row(
            "10:02:00",
            "update the page please",
            "I am updating it. I've added the title.",
            "delegate",
        ),
        row("10:03:00", "Boss.", "Looking up who Boss is.", "web_research"),
        row("10:04:00", "what is the weather in Cebu", "Today in Cebu: rain.", "weather"),
    ]
    found = oa.audit(rows)
    assert len(found["repeated_sentence"]) == 1
    assert len(found["duplicate_job"]) == 1 and "10:00:10" in found["duplicate_job"][0]
    assert len(found["premature_claim"]) == 1 and "10:02:00" in found["premature_claim"][0]
    assert found["fragment_tool"] == ["'Boss.' -> ['web_research']"]


def test_main_prints_a_line_per_check(tmp_path: Path, capsys: Any) -> None:
    log = tmp_path / "a.jsonl"
    log.write_text(json.dumps(row("10:00:00", "Boss.", "x", "web_research")) + "\n")
    assert oa.main([str(log)]) == 0
    out = capsys.readouterr().out
    assert "1 turns in 1 logs" in out and "fragment_tool" in out and "duplicate_job" in out
