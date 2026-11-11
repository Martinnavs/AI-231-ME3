"""Delegate MCP server: a stronger model works on a hard task in a sandbox, in the background.

uv run --group laptop python -m voice_agent.mcp_servers.delegate [--config config/delegate.toml]

The default model (config/delegate.toml, haiku-5.5 on OpenRouter) runs a tool loop over the `code`
server in one shared workspace (every job sees earlier jobs' files; a job's own folder keeps its
log); if it ends in an error the fallback (the class Qwen
server) runs the task once more. Keys are read at run time from files and never printed or put in
a result. A job that ends is recorded in the job table with a sentence to say (jobs.py); a job that
finishes while `delegate` is still waiting is answered in that call instead.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

from doc_chat.qwen import _read_secret_file, normalize_base_url
from voice_agent.agent.jobs import (
    CANCELLED,
    DONE,
    ERROR,
    FAILED,
    RUNNING,
    Job,
    JobTable,
    spoken,
)
from voice_agent.agent.mcp_clients import McpClientManager
from voice_agent.agent.subagent import Ledger, Limits, Model, RunResult, run_task
from voice_agent.config import McpServerSpec

log = logging.getLogger(__name__)
CONFIG = Path(__file__).resolve().parents[3] / "config" / "delegate.toml"
MAX_WAIT_S = 20.0
MAX_LISTED_FILES = 20
REASONS = {
    "step_limit": "it took too many steps",
    "time_limit": "it ran out of time",
    "token_limit": "it used up its context",
    "cost_limit": "it cost too much",
    "budget": "the delegate budget is used up",
}
STOPWORDS = frozenset(
    {"the", "and", "for", "with", "that", "this", "from", "into", "make", "write", "build"}
    | {"please", "task", "job", "one", "about", "using", "a", "an", "of", "to", "in", "on"}
)
Runner = Callable[[str, Model, str, Path, str], Awaitable[RunResult]]


def content_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOPWORDS}


# Words every game or script task uses: they do not say which task it is.
GENERIC = frozenset(
    {"create", "simple", "game", "using", "python", "html", "css", "javascript", "file", "files"}
    | {"folder", "named", "web", "version", "index", "apps", "app", "self", "contained", "playable"}
    | {"clone", "browser", "should", "main", "script", "save", "program", "output", "code", "run"}
    | {"list", "also", "produce", "all", "any", "browser", "page", "plain", "canvas", "wants"}
)
SAME_TASK = 0.6  # share of the smaller task's distinctive words that the other also has
SAME_TASK_MIN = 2  # fewer distinctive words than this cannot show two tasks are the same


def same_task(a: str, b: str) -> bool:
    """Two task texts that ask for the same thing: of the distinctive words (content words minus
    GENERIC) at least SAME_TASK of the smaller set are in the other. A mistranscribed repeat of
    "make a Flappy Bird game" is worded differently each time but keeps its subject words."""
    words_a, words_b = content_words(a) - GENERIC, content_words(b) - GENERIC
    smaller = min(len(words_a), len(words_b))
    return smaller >= SAME_TASK_MIN and len(words_a & words_b) / smaller >= SAME_TASK


APP_FOLDER = re.compile(r"\bapps/([A-Za-z0-9_-]+)")
FOLDER_NOISE = frozenset({"site", "page", "web", "app", "website", "demo", "new"})
STEM = 5  # letters that make "lighthouse" and "lighthouses" one word


def _folder_stems(task: str) -> set[str]:
    """The stems of the words in the apps/<name> folders a task names."""
    return {
        word[:STEM]
        for name in APP_FOLDER.findall(task)
        for word in re.split(r"[_-]", name.lower())
        if len(word) >= STEM and word not in FOLDER_NOISE
    }


def same_deliverable(a: str, b: str) -> bool:
    """Two tasks that write the same apps/ folder, or folders named alike (the front agent restates
    "also add X to it" as a whole new page in apps/<name>_site): one page, not two, or two jobs
    race on the same files (live, 2026-11-11)."""
    return bool(_folder_stems(a) & _folder_stems(b))


FOLLOWUP_MIN_NEW = 2  # new distinctive words that make an overlapping request an addition


# Politeness and pronouns: said around a repeat ("can you make me X right now"), never an addition.
CHATTER = frozenset(
    {"can", "could", "would", "you", "your", "me", "it", "its", "now", "right", "just", "really"}
    | {"thanks", "thank", "too", "then", "okay", "ok", "yes", "yeah", "hey", "again", "there", "so"}
)


def added_words(new: str, old: str) -> set[str]:
    """The distinctive words of `new` that `old` does not have: what a follow-up adds. A repeat
    wrapped in chatter ("can you make me X right now") adds none."""
    return (content_words(new) - GENERIC - CHATTER) - content_words(old)


def expand(path: str) -> Path:
    return Path(path).expanduser()


async def resolve_model(
    config: dict[str, Any], name: str, client: httpx.AsyncClient
) -> tuple[Model, str]:
    """The Model and API key of a configured backend (the class server's id is asked of it)."""
    spec = config["models"][name]
    if spec.get("secrets") == "onit":
        key = _read_secret_file("api_key.txt") or ""
        base = normalize_base_url(_read_secret_file("host_address.txt") or "")
        reply = await client.get(
            f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=10.0
        )
        model_id = reply.json()["data"][0]["id"]
    else:
        key = expand(spec["key_file"]).read_text().strip()
        base, model_id = spec["base_url"], spec["model"]
    prices = (spec.get("price_in", 0.0), spec.get("price_out", 0.0), spec.get("price_cached"))
    return Model(base, model_id, *prices, spec.get("extra", {})), key


SNAPSHOTS_KEPT = 10  # workspace snapshots kept, one per job


class Delegator:
    """The three tools' logic: jobs run as background tasks, each in its own sandbox folder."""

    def __init__(
        self,
        config: dict[str, Any],
        table: JobTable,
        run: Runner | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config, self.table, self.transport = config, table, transport
        self.run = run or self.run_in_sandbox
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.inline: set[str] = set()
        self.followups: dict[str, list[str]] = {}  # running job id -> additions to run after it
        paths = config["paths"]
        self.sandbox = expand(paths["sandbox_root"])
        self.workspace = expand(paths.get("workspace", "~/agent_sandbox/workspace"))
        self.pylibs = expand(paths["pylibs"]) if paths.get("pylibs") else None
        self.network = bool(paths.get("network", False))
        cap = config["limits"]
        self.limits = Limits(cap["max_steps"], cap["max_seconds"], max_usd=cap["max_usd"])
        self.wait_s, self.max_jobs = float(cap["wait_s"]), int(cap["max_jobs"])
        budgets = {"openrouter": cap["budget_usd"], "qwen": 1e9}
        self.ledger = Ledger(table.path.with_name("spend.json"), budgets)

    async def run_in_sandbox(
        self, task: str, model: Model, key: str, folder: Path, pool: str
    ) -> RunResult:
        args = ["-m", "voice_agent.mcp_servers.code", "--root", str(self.workspace)]
        if self.network:
            args.append("--network")
        if self.pylibs is not None:
            args += ["--pylibs", str(self.pylibs)]
        # the MCP client starts children with a minimal environment; the Pi has no editable install
        env = {"PYTHONPATH": os.environ["PYTHONPATH"]} if os.environ.get("PYTHONPATH") else {}
        servers = {"code": McpServerSpec(command=sys.executable, args=tuple(args), env=env)}
        if self.network:  # the web tools run here, on the host: the sandbox never sees the key
            servers["web"] = McpServerSpec(
                command=sys.executable,
                args=("-m", "voice_agent.mcp_servers.tavily_search"),
                env=env,
            )
        async with McpClientManager(servers) as registry:
            if "code" in registry.unavailable:
                return RunResult(status="error", summary="the code tools did not start")
            if "web" in registry.unavailable:
                log.warning("web tools unavailable, the job runs without them")
            tools = registry.subset(
                ["code__", "web__web_research", "web__web_answer"],
                exclude=["code__publish_site"],
            )
            async with httpx.AsyncClient(transport=self.transport, timeout=120.0) as client:
                return await run_task(
                    task,
                    model,
                    key,
                    tools,
                    self.limits,
                    client=client,
                    ledger=self.ledger,
                    pool=pool,
                )

    def snapshot(self, job_id: str, keep: int = SNAPSHOTS_KEPT) -> None:
        """A hard-link copy of the workspace before each job (sandbox_root/snapshots/<job>), so a
        job that deletes files by mistake can be undone; the oldest beyond `keep` are removed."""
        root = self.sandbox / "snapshots"
        if not self.workspace.is_dir():
            return
        root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["cp", "-al", str(self.workspace), str(root / job_id)], check=True)
        for old in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime)[:-keep]:
            shutil.rmtree(old, ignore_errors=True)

    async def work(self, job_id: str) -> None:
        job = self.table.jobs[job_id]
        names = [self.config["backend"]["default"], self.config["backend"].get("fallback")]
        result = RunResult()
        self.snapshot(job_id)
        for tried, name in enumerate(n for n in names if n):
            job.backend = name
            job.folder = str(self.sandbox / (job_id if tried == 0 else f"{job_id}-{name}"))
            Path(job.folder).mkdir(parents=True, exist_ok=True)
            self.workspace.mkdir(parents=True, exist_ok=True)
            try:
                async with httpx.AsyncClient(transport=self.transport) as client:
                    model, key = await resolve_model(self.config, name, client)
                pool = self.config["models"][name].get("pool", name)
                result = await self.run(job.task, model, key, Path(job.folder), pool)
            except Exception as exc:
                result = RunResult(status="error", summary=type(exc).__name__)
            if result.status not in ("error", "budget"):
                break
        done = result.status == "done"
        self.write_log(job, result)
        failed = result.status == "failed"
        summary = (
            result.summary if done or failed else REASONS.get(result.status, "something went wrong")
        )
        status = DONE if done else (FAILED if failed else ERROR)
        job = self.table.finish(job_id, status, summary, result.usd)
        if job_id not in self.inline:
            self.table.notify(job)
        self.start_followups(job)

    def start_followups(self, parent: Job) -> None:
        """Run what the user added while `parent` was running, as one job that updates its files.
        A request that overlaps a running job used to be dropped as a repeat, so a follow-up such
        as "also add public information" never reached the page (2026-11-11)."""
        queued = self.followups.pop(parent.id, [])
        if not queued or parent.status != DONE:  # nothing to build on when it did not finish
            return
        made = ", ".join(parent.files) or "its files"
        task = (
            f"This continues {parent.id}, which already made {made} in the shared workspace: "
            "update those files in place and keep what is there; do not start a new page. "
            "New request: " + " Also: ".join(queued)
        )
        job = self.table.create(task, self.config["backend"]["default"])
        self.tasks[job.id] = asyncio.create_task(self.work(job.id))

    def write_log(self, job: Job, result: RunResult) -> None:
        head = f"task: {job.task}\nbackend: {job.backend}\nstatus: {result.status}"
        text = f"{head}\n\n{result.summary}\n"
        with contextlib.suppress(OSError):
            (Path(job.folder) / "log.txt").write_text(text)

    def workspace_files(self) -> list[str]:
        """Relative paths of the shared workspace's files, shallow first, capped."""
        found: list[str] = []
        try:
            for path in sorted(self.workspace.rglob("*"), key=lambda p: (len(p.parts), str(p))):
                rel = path.relative_to(self.workspace)
                if path.is_file() and not any(
                    part.startswith(".") or part == "__pycache__" for part in rel.parts
                ):
                    found.append(rel.as_posix())
                if len(found) >= MAX_LISTED_FILES:
                    break
        except OSError:
            pass
        return found

    async def delegate(self, task: str, wait_s: float | None = None) -> dict[str, Any]:
        if sum(not worker.done() for worker in self.tasks.values()) >= self.max_jobs:
            message = f"Already working on {self.max_jobs} tasks; wait for one to finish."
            return {"status": "busy", "message": message}
        for running in self.table.jobs.values():
            worker = self.tasks.get(running.id)
            if (
                worker is not None
                and not worker.done()
                and (same_task(task, running.task) or same_deliverable(task, running.task))
            ):
                if len(added_words(task, running.task)) >= FOLLOWUP_MIN_NEW:
                    queued = self.followups.setdefault(running.id, [])
                    if not any(same_task(task, other) for other in queued):
                        queued.append(task)
                    return {
                        "status": "queued",
                        "job": running.id,
                        "message": f"{running.id} is still running; your addition is queued and "
                        "will update its result when it finishes.",
                    }
                return {
                    "status": "already_running",
                    "job": running.id,
                    "message": f"That is already running as {running.id}; wait for its result.",
                }
        wait = self.wait_s if wait_s is None else wait_s
        job = self.table.create(task, self.config["backend"]["default"])
        self.inline.add(job.id)
        worker = asyncio.create_task(self.work(job.id))
        self.tasks[job.id] = worker
        try:
            await asyncio.wait({worker}, timeout=max(0.0, min(wait, MAX_WAIT_S)))
        except asyncio.CancelledError:
            ended = self.table.jobs[job.id]
            if worker.done() and ended.status in (
                DONE,
                ERROR,
                FAILED,
            ):  # nobody will hear the answer
                self.table.notify(ended)
            raise
        finally:
            self.inline.discard(job.id)
        if worker.done():
            done = self.table.jobs[job.id]
            return {
                "job": job.id,
                "status": done.status,
                "summary": spoken(done.summary),
                "workspace_files": self.workspace_files(),
            }
        return {
            "job": job.id,
            "status": "started",
            "message": (
                "It is running in the background; the result is announced when it is done. "
                "Do not say how long it will take."
            ),
        }

    def resolve(self, job: str) -> Job | None:
        """Empty -> newest; a known id; else a description match (newest wins); else newest."""
        job = job.strip()
        if not job:
            return self.table.latest()
        if job in self.table.jobs:
            return self.table.jobs[job]
        newest_first = list(reversed(self.table.jobs.values()))
        wanted = job.lower()
        for candidate in newest_first:
            if wanted in candidate.task.lower():
                return candidate
        words, best, best_shared = content_words(job), None, 1
        for candidate in newest_first:
            shared = len(words & content_words(candidate.task))
            if shared > best_shared:
                best, best_shared = candidate, shared
        return best or self.table.latest()

    def status(self, job: str = "") -> dict[str, Any]:
        found = self.resolve(job)
        if found is None:
            return {"status": "none", "message": "There are no delegated tasks."}
        seconds = round((found.finished or time.time()) - found.started)
        return {
            "job": found.id,
            "status": found.status,
            "task": found.task[:120],
            "seconds": seconds,
            "summary": spoken(found.summary),
            "workspace_files": self.workspace_files(),
        }

    def cancel(self, job: str = "") -> dict[str, Any]:
        found = self.resolve(job)
        if found is None or found.status != RUNNING or found.id not in self.tasks:
            return {"status": "none", "message": "There is no running task to stop."}
        self.tasks[found.id].cancel()
        self.followups.pop(found.id, None)  # the additions were for a job that is no more
        self.table.finish(found.id, CANCELLED)
        return {"job": found.id, "status": CANCELLED, "task": found.task[:120]}


def build_server(delegator: Delegator) -> FastMCP:
    server = FastMCP("delegate")

    @server.tool(
        description=(
            "Hand a big task to a stronger background assistant that can run commands and write "
            "files in a sandbox: build a small website or app, solve a hard multi-step math "
            "problem with code, audit this machine, analyse files or data. Write `task` as the "
            "complete request with every detail it needs; it cannot ask questions. A quick task "
            "returns its result; otherwise it returns 'started' and the result is announced "
            "later; never say how long it will take. All tasks share one workspace, so a task "
            "can use files an earlier task wrote; name them in `task`. The sandbox has network "
            "(ping, curl, downloads work) and Python with pygame, but no screen, display, "
            "speakers or microphone and no sudo, so a game is tested headless. When the user wants "
            "something playable or visual (a game, a demo), say so in `task`: the task must also "
            "produce a self-contained web version at apps/<name>/index.html (<name> in snake_case, "
            "like flappy_bird, never spaces or hyphens; plain HTML, canvas "
            "and JavaScript, no external URLs); the user then plays it in their browser with "
            "open_app. If the user needs sudo or a device, say it cannot be done here instead of "
            "delegating. Never use it for simple questions, home commands, the "
            "weather or the time."
        )
    )
    async def delegate(task: str) -> dict[str, Any]:
        return await delegator.delegate(task)

    @server.tool(
        description=(
            "How the latest delegated task is going. `job` may be an id or a few words of its "
            "description; the reply names the task it found and lists the files in the shared "
            "workspace."
        )
    )
    def delegate_status(job: str = "") -> dict[str, Any]:
        return delegator.status(job)

    @server.tool(
        description=(
            "Stop the latest delegated task. `job` may be an id or a few words of its "
            "description; the reply names the task it stopped."
        )
    )
    def delegate_cancel(job: str = "") -> dict[str, Any]:
        return delegator.cancel(job)

    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voice_agent.mcp_servers.delegate")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--state-dir", default=None, help="jobs.json, notices.jsonl, spend.json")
    args = parser.parse_args(argv)
    config = tomllib.loads(args.config.read_text())
    table = JobTable(expand(args.state_dir or config["paths"]["state_dir"]))
    build_server(Delegator(config, table)).run(show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
