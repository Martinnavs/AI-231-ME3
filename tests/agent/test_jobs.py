from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from voice_agent.agent.jobs import DONE, ERROR, LOST, RUNNING, JobTable, listed_files, spoken

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "delegate_notices.jsonl"


def test_spoken_drops_the_files_line_and_files_are_listed() -> None:
    summary = "I built it.\nIt works.\nFiles: recipes/index.html, recipes/style.css"
    assert spoken(summary) == "I built it. It works."
    assert listed_files(summary) == ["recipes/index.html", "recipes/style.css"]
    assert listed_files("No files line.") == []
    assert listed_files("It failed.\nFiles: none") == []  # "none" once became the page to update


def test_create_finish_persist_and_reload(tmp_path: Path) -> None:
    table = JobTable(tmp_path)
    job = table.create("build a site", "haiku")
    assert (job.id, job.status) == ("job1", RUNNING) and table.latest() is job
    table.finish("job1", DONE, "Done.\nFiles: a.txt", 0.01)
    reloaded = JobTable(tmp_path)
    assert reloaded.jobs["job1"].status == DONE and reloaded.jobs["job1"].files == ["a.txt"]
    assert reloaded.create("next", "haiku").id == "job2"


def test_a_job_running_at_start_up_is_lost(tmp_path: Path) -> None:
    table = JobTable(tmp_path)
    table.create("slow", "haiku")
    assert JobTable(tmp_path).jobs["job1"].status == LOST


def test_notices_match_the_golden_file_the_pipeline_reads(tmp_path: Path) -> None:
    table = JobTable(tmp_path)
    table.create("site", "haiku")
    table.create("math", "haiku")
    table.notify(
        table.finish(
            "job1",
            DONE,
            "I built the recipes site. It is in the recipes folder.\nFiles: recipes/index.html",
        )
    )
    table.notify(table.finish("job2", ERROR, "it ran out of time"))
    assert table.notices.read_text() == GOLDEN.read_text()
    assert [json.loads(line)["job"] for line in GOLDEN.read_text().splitlines()] == ["job1", "job2"]


def test_two_processes_can_open_the_same_state_folder(tmp_path: Path) -> None:
    code = (
        "import sys\nfrom pathlib import Path\nfrom voice_agent.agent.jobs import JobTable\n"
        "for _ in range(300):\n    JobTable(Path(sys.argv[1]))\n"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    assert [proc.wait(timeout=120) for proc in procs] == [0, 0]
