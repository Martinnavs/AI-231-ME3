import json
import shutil
from pathlib import Path

from voice_pipeline.notices import NoticeQueue

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "delegate_notices.jsonl"


def test_reads_the_golden_file_the_delegate_server_tests_pin(tmp_path: Path) -> None:
    path = tmp_path / "notices.jsonl"
    queue = NoticeQueue(path)  # the file does not exist yet
    assert queue.pop() == []
    shutil.copy(GOLDEN, path)
    assert queue.pop() == [
        "I built the recipes site. It is in the recipes folder.",
        "I could not finish job2: it ran out of time.",
    ]
    assert queue.pop() == []


def test_starts_at_the_end_of_an_old_file_and_waits_for_a_complete_line(tmp_path: Path) -> None:
    path = tmp_path / "notices.jsonl"
    path.write_text(json.dumps({"job": "job1", "text": "old"}) + "\n")
    queue = NoticeQueue(path)
    assert queue.pop() == []
    line = json.dumps({"job": "job2", "text": "new"}) + "\n"
    with path.open("a") as sink:
        sink.write(line[:10])
    assert queue.pop() == []
    with path.open("a") as sink:
        sink.write(line[10:])
    assert queue.pop() == ["new"]
