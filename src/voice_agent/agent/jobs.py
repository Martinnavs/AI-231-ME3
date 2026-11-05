"""Job table of delegated tasks: jobs.json, and notices.jsonl for what to say when one ends."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

RUNNING, DONE, ERROR, CANCELLED, LOST = "running", "done", "error", "cancelled", "lost"
FAILED = "failed"  # ran to an end but produced nothing usable (a cut-off reply, no result)


@dataclass
class Job:
    id: str
    task: str
    backend: str
    folder: str = ""
    status: str = RUNNING
    summary: str = ""
    files: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    usd: float = 0.0


def task_name(task: str) -> str:
    """The first words of the task, for 'The <task> job didn't finish'."""
    return " ".join(task.split()[:5]).rstrip(",.;:") or "background"


def spoken(summary: str) -> str:
    """The summary without its 'Files:' line: what is said aloud."""
    lines = (ln.strip() for ln in summary.splitlines())
    return " ".join(ln for ln in lines if ln and not ln.startswith("Files:"))


def listed_files(summary: str) -> list[str]:
    for line in reversed(summary.splitlines()):
        if line.startswith("Files:"):
            return [name.strip() for name in line[6:].split(",") if name.strip()]
    return []


class JobTable:
    """Jobs persisted after every change; a job still running when the table is opened is lost."""

    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self.path = state_dir / "jobs.json"
        self.notices = state_dir / "notices.jsonl"
        self.counter = 0
        self.jobs: dict[str, Job] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.counter = data["counter"]
            self.jobs = {row["id"]: Job(**row) for row in data["jobs"]}
        for job in self.jobs.values():
            if job.status == RUNNING:
                job.status = LOST
        self.save()

    def save(self) -> None:
        rows = [asdict(job) for job in self.jobs.values()]
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"counter": self.counter, "jobs": rows}))
        os.replace(tmp, self.path)

    def create(self, task: str, backend: str) -> Job:
        self.counter += 1
        job = Job(f"job{self.counter}", task, backend)
        self.jobs[job.id] = job
        self.save()
        return job

    def finish(self, job_id: str, status: str, summary: str = "", usd: float = 0.0) -> Job:
        job = self.jobs[job_id]
        job.status, job.summary, job.usd = status, summary, usd
        job.files, job.finished = listed_files(summary), time.time()
        self.save()
        return job

    def notify(self, job: Job) -> None:
        said = spoken(job.summary)
        text = (
            said if job.status == DONE else f"The {task_name(job.task)} job didn't finish: {said}."
        )
        with self.notices.open("a") as sink:
            sink.write(json.dumps({"job": job.id, "text": text}) + "\n")

    def latest(self) -> Job | None:
        return list(self.jobs.values())[-1] if self.jobs else None
