"""Generic chain evaluation: do the chain mechanisms hold across tasks, deliverables and topics?

Live (real Gemini front agent, Tavily, the Haiku delegate in a scratch workspace; spends money):

    pipeline/.venv/bin/python scripts/chain_eval.py --list
    pipeline/.venv/bin/python scripts/chain_eval.py [--only ID,ID] [--trials N] [--max-usd 0.25]

No network, no cost (fakes; this is what the unit tests run):

    .venv/bin/python scripts/chain_eval.py --dry

Each scenario (config/eval/chain_scenarios.jsonl) is a few user turns with random parameters, so a
cached answer is not reused, and the predicates it declares in `expect`. A trace of every turn is
checked with the generic predicates below; output is one JSON line per scenario-trial in
out/chain_eval/<run-id>/trials.jsonl plus a table. The shared spend ledger stops the run at 90 %
of --max-usd; the estimate (about $0.02 per scenario-trial) is printed before any call.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import random
import re
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "config/eval/chain_scenarios.jsonl"
OUT = ROOT / "out/chain_eval"
LEDGER = OUT / "ledger.json"
USD_PER_TRIAL = 0.02
STOP_AT = 0.9
JOB_WAIT_S = 300.0
LOOKUPS = frozenset({"web_research", "web_answer", "wiki_summary"})
WEB = frozenset({"web_research", "web_answer"})
RECENCY_WORDS = re.compile(r"\b(latest|next|current|upcoming|today)\b", re.IGNORECASE)
URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_UNUSED_SOURCE = re.compile(r"\b(?:not used|unused|did not use|unreferenced)\b", re.IGNORECASE)
PREFIXES = ("Unsourced: ", "Unverified content: ")
ASKED = re.compile(r"\?|which|assum", re.IGNORECASE)

Trace = dict[str, Any]
Runner = Callable[["Scenario", dict[str, str], int], Awaitable[Trace]]


@dataclass(frozen=True)
class Scenario:
    id: str
    kind: str
    deliverable: str
    recency: bool
    turns: list[str]
    params: dict[str, list[str]]
    expect: dict[str, Any]
    overlap: bool = False  # the next turn is spoken while the build job still runs


def load_scenarios(path: Path = SCENARIOS) -> list[Scenario]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [
        Scenario(
            r["id"],
            r["kind"],
            r.get("deliverable", "none"),
            bool(r.get("recency")),
            r["turns"],
            r.get("params", {}),
            r.get("expect", {}),
            bool(r.get("overlap")),
        )
        for r in rows
    ]


def pick_params(scenario: Scenario, trial: int, seed: int) -> dict[str, str]:
    rng = random.Random(f"{seed}:{scenario.id}:{trial}")
    return {name: rng.choice(options) for name, options in scenario.params.items()}


def fill(turn: str, params: dict[str, str]) -> str:
    return turn.format_map(params)


# --- predicates: each takes a trace and returns True/False, or None when it does not apply -----


def _calls(trace: Trace) -> list[dict[str, Any]]:
    return [c for t in trace["turns"] for c in t["calls"]]


def recency_tool(trace: Trace) -> bool | None:
    """A recency question needs a date-aware lookup: web_research(recent=true), or web_answer
    (the grounded path knows the date) for a single fact; no lookup at all fails."""
    if not any(RECENCY_WORDS.search(t["user"]) for t in trace["turns"]):
        return None
    return any(
        (c["tool"] == "web_research" and c["args"].get("recent") is True)
        or c["tool"] == "web_answer"
        for c in _calls(trace)
    )


def max_lookups_per_turn(trace: Trace, limit: int = 1) -> bool:
    return all(
        sum(c["tool"] in LOOKUPS and not c.get("cached") for c in t["calls"]) <= limit
        for t in trace["turns"]
    )


def _build_turn(trace: Trace) -> dict[str, Any] | None:
    return next(
        (t for t in trace["turns"] if any(c["tool"] == "delegate" for c in t["calls"])), None
    )


def chain_not_idle_before_build(trace: Trace) -> bool | None:
    turn = _build_turn(trace)
    return None if turn is None else turn["chain_phase"] != "idle"


def delegate_task_has_facts_or_url(trace: Trace) -> bool | None:
    """The model's pre-injection delegate task carries grounded facts, numbers, dates, or a URL."""
    turn = _build_turn(trace)
    if turn is None:
        return None
    call = next((c for c in turn["calls"] if c["tool"] == "delegate"), None)
    if call is None:
        return None
    # Inspect raw pre-injection task if recorded
    task = str(call.get("raw_task") or call["args"].get("task", ""))
    if "raw_task" not in call:
        for marker in (
            "Notes from the conversation (unchecked):",
            "Verified facts from conversation:",
            "Primary source URLs:",
        ):
            if marker in task:
                task = task.split(marker)[0].strip()

    if not task:
        return False

    if bool(URL.search(task)):
        return True
    if any(u in task for u in trace.get("web_urls", []) if u):
        return True
    if "/sandbox/scratch/NOTES.md" in task:
        return True

    scratch = trace.get("scratch") or {}
    scratch_text = str(scratch.get("text", ""))
    if not scratch_text:
        return False

    user_text = " ".join(t.get("user", "") for t in trace.get("turns", []))
    user_words = set(re.findall(r"[a-zA-Z0-9_-]+", user_text.lower()))

    # Grounded dates, years, versions, or numeric figures from scratchpad
    num_patterns = set(
        re.findall(
            r"\b(?:v?\d+\.\d+(?:\.\d+)*|(?:18|19|20)\d{2}|"
            r"\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
            r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
            r"Dec(?:ember)?)|"
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
            r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
            r"\d{1,2}(?:st|nd|rd|th)?(?:\s*,\s*\d{4})?|"
            r"\d+(?:\.\d+)?%|\d+[- ](?:inch|bit|px|ppi|gb|mb)|\b\d{2,}\b)\b",
            scratch_text,
            re.IGNORECASE,
        )
    )
    task_lower = task.lower()
    for num in num_patterns:
        if len(num) >= 2 and num.lower() in task_lower and num.lower() not in user_words:
            return True

    # Grounded technical identifiers (e.g. LTS, iPadOS, EPUB, packages)
    tech_ids = set(
        re.findall(
            r"\b(?:[A-Z][a-zA-Z0-9]+[A-Z][a-zA-Z0-9]*|[A-Z]{3,}|"
            r"[a-zA-Z0-9_-]+/[a-zA-Z0-9_/-]+)\b",
            scratch_text,
        )
    )
    generic_stops = {
        "table",
        "markdown",
        "html",
        "page",
        "create",
        "build",
        "make",
        "compare",
        "comparing",
        "saved",
        "file",
        "apps",
        "index",
        "details",
        "features",
        "differences",
        "history",
        "verified",
        "facts",
        "self-contained",
        "javascript",
        "css",
        "deliverable",
        "clean",
        "professional",
        "colors",
        "design",
        "official",
        "one-page",
    }
    for tid in tech_ids:
        if (
            tid.lower() not in user_words
            and tid.lower() not in generic_stops
            and len(tid) >= 3
            and re.search(r"\b" + re.escape(tid) + r"\b", task, re.IGNORECASE)
        ):
            return True

    # Grounded substantive terms from web_answer (for comparison scenarios)
    answer_sections = re.findall(
        r"## \d{2}:\d{2} web_answer:.*?\n(.*?)(?=\n##|\Z)", scratch_text, re.DOTALL
    )
    if answer_sections:
        clean_answers = re.sub(r"https?://\S+", " ", " ".join(answer_sections))
        answer_words = set(re.findall(r"\b[a-zA-Z]{5,}\b", clean_answers.lower()))
        substantive_answer = answer_words - user_words - generic_stops
        task_words = set(re.findall(r"\b[a-zA-Z]{5,}\b", task_lower))
        if len(task_words & substantive_answer) >= 2:
            return True

    return False


def deliverable_sources(trace: Trace) -> bool | None:
    """SOURCES.json is non-empty, every url is http(s) with a path (no bare domain), and facts
    are not unused; each url was seen in a front-agent result/scratchpad or summary names sources.
    """
    if not trace["files"]["text_deliverables"]:
        return None
    try:
        rows = json.loads(trace["files"]["SOURCES.json"] or "")
    except (ValueError, TypeError):
        return False
    if not isinstance(rows, list) or not rows:
        return False
    urls: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            return False
        u = str(r.get("url", ""))
        fact = str(r.get("fact", ""))
        if not re.match(r"https?://", u):
            return False
        parsed = urlsplit(u)
        if not parsed.path or parsed.path == "/":
            return False
        if _UNUSED_SOURCE.search(fact):
            return False
        urls.append(u)
    scratch = trace.get("scratch") or {}
    seen = "\n".join([*trace["web_urls"], str(scratch.get("text", ""))])
    summary = str((trace.get("job") or {}).get("summary", ""))
    return all(u in seen for u in urls) or bool(re.search(r"\bsources?\b", summary, re.IGNORECASE))


def lint_clean(trace: Trace) -> bool | None:
    job = trace.get("job")
    if job is None or not trace["files"]["text_deliverables"]:
        return None
    summary = str(job.get("summary", ""))
    return summary.startswith(PREFIXES) or trace.get("lint_flags", 0) == 0


def no_gate_for_code_only(trace: Trace) -> bool:
    job = trace.get("job") or {}
    return (
        not str(job.get("summary", "")).startswith(PREFIXES)
        and trace["files"].get("SOURCES.json") is None
        and not trace.get("lint_flags")
    )  # a playable page is a text file too: what matters is that no source check was asked for


def no_web_for_plain(trace: Trace) -> bool:
    return not any(c["tool"] in WEB for c in _calls(trace))


def no_chain_for_followup(trace: Trace) -> bool:
    quiet = {"idle", ""}
    return all(t["chain_phase"] in quiet for t in trace["turns"]) and not any(
        c["tool"] in ("chain_update", "delegate") for c in _calls(trace)
    )


def playable_sentence(trace: Trace) -> bool | None:
    job = trace.get("job")
    return None if job is None else "web version is ready" in str(job.get("summary", ""))


def asked_or_flagged(trace: Trace) -> bool:
    return any(ASKED.search(t["reply"]) for t in trace["turns"])


def scratchpad_has_findings(trace: Trace) -> bool | None:
    """After the first lookup the notes file exists and holds every lookup's query."""
    queries = [
        str(c["args"].get("query") or c["args"].get("topic") or "")
        for c in _calls(trace)
        if c["tool"] in LOOKUPS and not c.get("cached")
    ]
    if not queries:
        return None
    scratch = trace.get("scratch") or {}
    text = " ".join(str(scratch.get("text", "")).split())
    return bool(scratch.get("exists")) and all(" ".join(q.split())[:200] in text for q in queries)


def delegate_reads_notes(trace: Trace) -> bool | None:
    """The delegate task carries the notes sentence when the notes had entries."""
    turn = _build_turn(trace)
    if turn is None or not turn.get("scratch_entries"):
        return None
    task = next(c["args"].get("task", "") for c in turn["calls"] if c["tool"] == "delegate")
    return "/sandbox/scratch/NOTES.md" in task


def followup_merged(trace: Trace) -> bool | None:
    """A request added while a job runs is queued, then runs as ONE more job that continues the
    first (not dropped as a repeat, not a second page): exactly two jobs, the second says so."""
    jobs = trace.get("jobs")
    if jobs is None:
        return None
    return len(jobs) == 2 and "continues" in str(jobs[1].get("task", "")).lower()


def read_without_delegate(trace: Trace) -> bool | None:
    """ "Read it to me" about a file already made is answered with read_file in the same turn:
    no delegate job (slow, billed, announced minutes later) just to read a file."""
    if len(trace["turns"]) < 2:
        return None
    tools = [c["tool"] for c in trace["turns"][-1]["calls"]]
    return "open_app" in tools and "delegate" not in tools  # open_app on a file name reads it


def answered_by_web_answer(trace: Trace) -> bool:
    """One fact, even a current one (who holds a role now): the grounded web_answer, once, not
    several web_research searches (eight in a row in the live session of 2026-11-11)."""
    tools = [c["tool"] for c in _calls(trace)]
    return tools.count("web_answer") == 1 and "web_research" not in tools


def build_about_latest_topic(trace: Trace, spec: dict[str, Any]) -> bool | None:
    """A build that says "their ..." is about what was just researched, not about an earlier
    topic in the conversation: the delegate task names spec["must"] and none of spec["forbid"]
    (live 2026-11-12: the five Metro Manila cities became Cebu, Tagaytay and Baguio, the cities of
    the weather questions before them)."""
    turn = _build_turn(trace)
    if turn is None:
        return None
    task = next(c["args"].get("task", "") for c in turn["calls"] if c["tool"] == "delegate").lower()
    return str(spec["must"]).lower() in task and not any(
        str(w).lower() in task for w in spec["forbid"]
    )


def _present(item: str, text: str) -> bool:
    low = item.lower()
    words = re.findall(r"[a-z0-9]{3,}", low)
    return low in text or bool(words and max(words, key=len) in text)


def _after_build(trace: Trace) -> list[dict[str, Any]]:
    turns = trace["turns"]
    first = next(
        (i for i, t in enumerate(turns) if any(c["tool"] == "delegate" for c in t["calls"])), None
    )
    return [] if first is None else turns[first + 1 :]


def items_kept(trace: Trace) -> bool | None:
    """Every item of the working set after the first build is still in the final deliverable,
    whatever the topic (2026-11-12: five cities became three after two updates)."""
    turns = trace["turns"]
    first = next((t for t in turns if t.get("chain_items")), None)
    if first is None:
        return False if _build_turn(trace) else None  # a build with no working set recorded
    text = str(trace["files"].get("text", "")).lower()
    return len(first["chain_items"]) >= 3 and all(_present(i, text) for i in first["chain_items"])


def no_lookup_on_refinement(trace: Trace) -> bool | None:
    """Changing a finished deliverable does not search again or read results aloud."""
    later = _after_build(trace)
    return None if not later else not any(c["tool"] in LOOKUPS for t in later for c in t["calls"])


def refinement_is_patch(trace: Trace) -> bool | None:
    """Each refinement turn delegates an update of the existing file (the harness adds the
    sentence that names the file and the items to keep)."""
    later = _after_build(trace)
    if not later:
        return None
    return all(
        any(
            c["tool"] == "delegate" and "Update the existing" in str(c["args"].get("task", ""))
            for c in t["calls"]
        )
        for t in later
    )


ACK_TURN = re.compile(r"\b(?:got it|thanks?|thank you|understood|mm+[- ]?hm+)\b", re.IGNORECASE)


def acks_start_nothing(trace: Trace) -> bool | None:
    """ "Got it, thanks" is not a request: no job and no lookup (a job was started for "Yep, we
    got it", 2026-11-12)."""
    acks = [t for t in trace["turns"] if ACK_TURN.search(t["user"]) and len(t["user"].split()) <= 7]
    if not acks:
        return None
    return not any(
        c["tool"] in LOOKUPS or c["tool"] == "delegate" for t in acks for c in t["calls"]
    )


def last_turn_is_patch(trace: Trace) -> bool | None:
    """After side questions and an acknowledgement the refinement still reaches the finished page
    as an update that keeps the items: the task survived the side turns."""
    turns = trace["turns"]
    if _build_turn(trace) is None or len(turns) < 3:
        return None
    return any(
        c["tool"] == "delegate" and "Update the existing" in str(c["args"].get("task", ""))
        for c in turns[-1]["calls"]
    )


def restatement_replaces(trace: Trace) -> bool | None:
    """ "Sorry, I mean ..." within seconds of a job's start cancels that job and starts one new
    one (before: a second and a third job ran beside it): two jobs, the first cancelled, the
    second a fresh task and not a continuation."""
    jobs = trace.get("jobs")
    if jobs is None:
        return None
    return (
        len(jobs) == 2
        and jobs[0].get("status") == "cancelled"
        and "continues" not in str(jobs[1].get("task", "")).lower()
    )


def job_count(trace: Trace, wanted: int) -> bool | None:
    """Exactly `wanted` jobs ran: a repeat is dropped (1), two different builds both run (2)."""
    jobs = trace.get("jobs")
    return None if jobs is None else len(jobs) == wanted


def independent_jobs(trace: Trace) -> bool | None:
    """No job is a continuation of another: unrelated builds must not be merged."""
    jobs = trace.get("jobs")
    if not jobs:
        return None
    return not any("continues job" in str(j.get("task", "")).lower() for j in jobs)


def first_job_cancelled(trace: Trace) -> bool | None:
    """ "Stop the job" while it runs ends it, and starts nothing else."""
    jobs = trace.get("jobs")
    if not jobs:
        return None
    return len(jobs) == 1 and jobs[0].get("status") == "cancelled"


def deliverable_has_marker(trace: Trace) -> bool | None:
    """What the follow-up asked to add (params["marker"]) is in a deliverable, so it was applied."""
    marker = str(trace["params"].get("marker", "")).lower()[:5]
    if not marker or trace.get("job") is None:
        return None
    return marker in str(trace["files"].get("text", "")).lower()


def no_stale_context(trace: Trace) -> bool | None:
    """A build about something else must not carry the earlier research: neither the notes
    sentence nor that topic's words reach the delegate task."""
    turn = _build_turn(trace)
    if turn is None:
        return None
    task = next(c["args"].get("task", "") for c in turn["calls"] if c["tool"] == "delegate")
    old = str(trace["params"].get("topic", "")).lower()
    return "/sandbox/scratch/NOTES.md" not in task and (not old or old not in task.lower())


def no_stray_apps(trace: Trace) -> bool:
    """Non-playable deliverables must not write stray files into apps/."""
    apps_files = trace["files"].get("apps", [])
    return len(apps_files) == 0


def delegate_called(trace: Trace) -> bool:
    """A build request must invoke the delegate tool, never stalling or surrendering."""
    return any(c["tool"] == "delegate" for c in _calls(trace))


PREDICATES: dict[str, Callable[..., bool | None]] = {
    "delegate_called": delegate_called,
    "recency_tool": recency_tool,
    "max_lookups_per_turn": max_lookups_per_turn,
    "chain_not_idle_before_build": chain_not_idle_before_build,
    "delegate_task_has_facts_or_url": delegate_task_has_facts_or_url,
    "deliverable_sources": deliverable_sources,
    "lint_clean": lint_clean,
    "no_gate_for_code_only": no_gate_for_code_only,
    "no_web_for_plain": no_web_for_plain,
    "no_chain_for_followup": no_chain_for_followup,
    "playable_sentence": playable_sentence,
    "scratchpad_has_findings": scratchpad_has_findings,
    "delegate_reads_notes": delegate_reads_notes,
    "no_stray_apps": no_stray_apps,
    "followup_merged": followup_merged,
    "answered_by_web_answer": answered_by_web_answer,
    "items_kept": items_kept,
    "no_lookup_on_refinement": no_lookup_on_refinement,
    "refinement_is_patch": refinement_is_patch,
    "acks_start_nothing": acks_start_nothing,
    "last_turn_is_patch": last_turn_is_patch,
    "build_about_latest_topic": build_about_latest_topic,
    "restatement_replaces": restatement_replaces,
    "job_count": job_count,
    "read_without_delegate": read_without_delegate,
    "independent_jobs": independent_jobs,
    "first_job_cancelled": first_job_cancelled,
    "deliverable_has_marker": deliverable_has_marker,
    "no_stale_context": no_stale_context,
    "asked_or_flagged": asked_or_flagged,
}
REPORT_ONLY = frozenset({"asked_or_flagged"})  # shown, never scored


def score(scenario: Scenario, trace: Trace) -> dict[str, bool | None]:
    """Declared predicates only: True pass, False fail, None not applicable."""
    results: dict[str, bool | None] = {}
    for name, wanted in scenario.expect.items():
        if not wanted:
            continue
        args = [] if isinstance(wanted, bool) else [wanted]
        results[name] = PREDICATES[name](trace, *args)
    return results


def trial_passes(results: dict[str, bool | None]) -> bool:
    return all(v is not False for k, v in results.items() if k not in REPORT_ONLY)


# --- budget ---------------------------------------------------------------------------------


@dataclass
class Budget:
    cap: float
    ledger: Path
    spent: float = 0.0

    def __post_init__(self) -> None:
        if self.ledger.exists():
            self.spent = float(json.loads(self.ledger.read_text())["usd"])

    def allows(self, next_usd: float = USD_PER_TRIAL) -> bool:
        return self.spent + next_usd < STOP_AT * self.cap

    def add(self, usd: float) -> None:
        self.spent += usd
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps({"usd": round(self.spent, 6)}))


def estimate(scenarios: Sequence[Scenario], trials: int) -> float:
    return len(scenarios) * trials * USD_PER_TRIAL


# --- runners --------------------------------------------------------------------------------


def _trace(scenario: Scenario, params: dict[str, str], trial: int) -> Trace:
    return {
        "id": scenario.id,
        "trial": trial,
        "params": params,
        "turns": [],
        "files": {"SOURCES.json": None, "text_deliverables": [], "apps": []},
        "web_urls": [],
        "job": None,
        "jobs": None,
        "scratch": {"exists": False, "text": ""},
        "lint_flags": 0,
        "spend": 0.0,
        "workspace": None,
    }


async def dry_runner(scenario: Scenario, params: dict[str, str], trial: int) -> Trace:
    """Fakes that behave as the mechanisms intend, so the predicates and the report run offline."""
    trace = _trace(scenario, params, trial)
    url = "https://example.org/source"
    phase = "idle"
    notes: list[str] = []
    builds = scenario.kind in (
        "research_build",
        "code_only",
        "plain",
        "unrelated_build",
        "overlap_build",
        "readback",
        "refine_build",
        "refine_after_side",
    )
    for index, template in enumerate(scenario.turns):
        user = fill(template, params)
        last = index == len(scenario.turns) - 1
        turn_phase = phase
        calls: list[dict[str, Any]] = []
        if scenario.kind in ("research_build", "unrelated_build") and not notes:
            recent = RECENCY_WORDS.search(user) is not None
            calls.append({"tool": "web_research", "args": {"query": user, "recent": recent}})
            notes.append(f"## 00:00 web_research: {user}\n{url}")
            trace["web_urls"].append(url)
            phase = "researching"
        if scenario.kind == "refine_after_side" and 0 < index < len(scenario.turns) - 1:
            side = [] if ACK_TURN.search(user) else [{"tool": "weather", "args": {"place": "x"}}]
            trace["turns"].append(
                {
                    "user": user,
                    "calls": side,
                    "reply": "ok",
                    "chain_phase": "idle",
                    "scratch_entries": 0,
                    "chain_items": ["Alpha", "Beta", "Gamma"],
                }
            )
            continue
        if scenario.kind in ("refine_build", "refine_after_side"):
            task = "Build a page of three things: Alpha, Beta, Gamma."
            if index:
                task = "Update the existing apps/x/index.html in place. Keep ALL of these items."
            else:
                calls.append({"tool": "web_answer", "args": {"query": user}})
            calls.append({"tool": "delegate", "args": {"task": task}, "raw_task": task})
            trace["turns"].append(
                {
                    "user": user,
                    "calls": calls,
                    "reply": "Started.",
                    "chain_phase": "idle",
                    "scratch_entries": 0,
                    "chain_items": ["Alpha", "Beta", "Gamma"],
                }
            )
            continue
        if scenario.kind == "lookup":
            calls.append({"tool": "web_answer", "args": {"query": user}})
        elif scenario.kind == "readback" and last:
            calls.append({"tool": "open_app", "args": {"name": "note.md"}})
        elif scenario.kind == "readback":
            task = "Write note.md."
            calls.append({"tool": "delegate", "args": {"task": task}, "raw_task": task})
        elif scenario.kind == "overlap_build" and not last:
            task = f"Build a one-page site. Source {url}"
            calls.append({"tool": "delegate", "args": {"task": task}, "raw_task": task})
        elif builds and last:
            about = (scenario.expect.get("build_about_latest_topic") or {}).get("must", "x")
            task = f"Build the deliverable from these verified facts: {about}. Source {url}"
            if notes and scenario.kind != "unrelated_build":
                task += " Notes and sources from the conversation are in /sandbox/scratch/NOTES.md."
            calls.append({"tool": "delegate", "args": {"task": task}, "raw_task": task})
        elif (scenario.kind in ("research_build", "code_only") and not calls) or (
            scenario.kind == "followup" and index == 0
        ):
            recent = RECENCY_WORDS.search(user) is not None
            calls.append({"tool": "web_research", "args": {"query": user, "recent": recent}})
            notes.append(f"## 00:00 web_research: {user}\n{url}")
            trace["web_urls"].append(url)
        reply = "Which one do you mean?" if not last else "Done."
        trace["turns"].append(
            {
                "user": user,
                "calls": calls,
                "reply": reply,
                "chain_phase": turn_phase,
                "scratch_entries": len(notes),
            }
        )
        if scenario.kind in ("research_build", "unrelated_build"):
            phase = "researching"
    trace["scratch"] = {"exists": bool(notes), "text": "\n".join(notes)}
    if builds:
        text = scenario.deliverable in ("html", "md")
        game = scenario.deliverable == "game"
        trace["job"] = {
            "status": "done",
            "summary": "The snake web version is ready; say open it to play it."
            if game
            else "Built it from sources.",
        }
        trace["files"] = {
            "SOURCES.json": json.dumps([{"url": url, "fact": "x"}]) if text else None,
            "text_deliverables": [f"out.{scenario.deliverable}"] if text else [],
            "apps": ["snake_game/index.html"] if game else [],
            "text": (
                "Alpha Beta Gamma"
                if scenario.kind in ("refine_build", "refine_after_side")
                else str(params.get("marker", ""))
            )
            if text
            else "",
        }
        if scenario.overlap:
            expect = scenario.expect
            first = {"id": "job1", "task": "Build a one-page site.", "status": "done"}
            if "restatement_replaces" in expect:
                trace["jobs"] = [
                    {**first, "status": "cancelled"},
                    {"id": "job2", "task": "Build another page.", "status": "done"},
                ]
            elif "first_job_cancelled" in expect:
                trace["jobs"] = [{**first, "status": "cancelled"}]
            elif "job_count" in expect:
                trace["jobs"] = [first, {**first, "id": "job2"}][: int(expect["job_count"])]
            else:
                trace["jobs"] = [
                    first,
                    {"id": "job2", "task": "This continues job1 ... update those files in place."},
                ]
    return trace


def _urls(text: str) -> list[str]:
    return URL.findall(text)


def make_live_runner(run_dir: Path, seed: int) -> Runner:
    """The real stack, imported lazily so the module loads without pipecat (run it with
    pipeline/.venv/bin/python)."""
    sys.path.insert(0, str(ROOT / "pipeline/src"))

    async def runner(scenario: Scenario, params: dict[str, str], trial: int) -> Trace:
        import tomllib

        load = importlib.import_module  # pipecat lives only in the pipeline venv
        agent_tools = load("voice_pipeline.agent_tools")
        agent_mod = load("voice_pipeline.agent")
        Agent, build_llm = agent_mod.Agent, agent_mod.build_llm
        AgentServer = load("voice_pipeline.agent_server").AgentServer
        chain_module = load("voice_pipeline.chain_state")
        ChainState, is_followup = chain_module.ChainState, chain_module.is_followup
        build_tools, standard_servers = agent_tools.build_tools, agent_tools.standard_servers
        without_tools, MODEL_HIDDEN = agent_tools.without_tools, agent_tools.MODEL_HIDDEN

        base = run_dir / f"{scenario.id}-{trial}"
        state, workspace = base / "state", base / "workspace"
        for folder in (state, workspace, base / "jobs"):
            folder.mkdir(parents=True, exist_ok=True)
        config = (ROOT / "config/delegate.toml").read_text()
        paths = tomllib.loads(config)["paths"]
        for key, folder in (
            ("state_dir", state),
            ("sandbox_root", base / "jobs"),
            ("workspace", workspace),
        ):
            config = config.replace(f'{key} = "{paths[key]}"', f'{key} = "{folder}"')
        (base / "delegate.toml").write_text(config)
        agent_tools.configure(
            delegate_state_dir=str(state), delegate_config=str(base / "delegate.toml")
        )
        trace = _trace(scenario, params, trial)
        trace["workspace"] = str(workspace)
        record: list[dict[str, Any]] = []
        chain = ChainState(state)
        pad = load("voice_pipeline.scratchpad").Scratchpad(workspace / "scratch" / "NOTES.md")
        chain.on_change = lambda event: pad.reset() if event == "end" else None
        servers, optional = standard_servers(None)
        tools = await build_tools(
            record, servers=servers, optional=optional, chain=chain, scratchpad=pad
        )
        FunctionSchema = load("pipecat.adapters.schemas.function_schema").FunctionSchema
        ToolsSchema = load("pipecat.adapters.schemas.tools_schema").ToolsSchema
        wrapped = []
        for schema in tools.standard_tools:  # note the URLs the front agent's lookups returned
            if schema.name in WEB and schema.handler is not None:
                schema = FunctionSchema(  # handler is read-only: build a copy that records URLs
                    name=schema.name,
                    description=schema.description,
                    properties=schema.properties,
                    required=schema.required,
                    handler=_capture_urls(schema.handler, trace["web_urls"]),
                )
            wrapped.append(schema)
        tools = ToolsSchema(standard_tools=wrapped)
        system = (ROOT / "pipeline/config/eval/system_agent.txt").read_text()
        block_file = ROOT / "pipeline/config/eval/system_agent_case_f.txt"
        case_f_block = block_file.read_text() if block_file.exists() else ""
        agent = Agent(build_llm(system), without_tools(tools, MODEL_HIDDEN), usage=True)
        server = AgentServer(
            agent, record, base / "log.jsonl", chain=chain, case_f_block=case_f_block
        )
        await agent.start()
        history: list[dict[str, Any]] = []
        spent0 = dict(agent.tap.usage)
        try:
            for turn in scenario.turns:
                user = fill(turn, params)
                if (
                    chain.active()
                    and chain.data["done"]
                    and not is_followup(user)
                    and not chain_module.is_side_question(user)
                ):
                    chain.update(
                        "idle"
                    )  # as agent_server.handle: a finished task ends on a new topic
                phase = chain.data["phase"]
                # mirrors the production rule: the Case F protocol joins while a chain is active
                steered = server.steer(
                    [*history, {"role": "user", "content": user}], case_f=chain.active()
                )
                agent_tools.reset_turn_lookups()
                question_state: Any = load("voice_pipeline.question")
                question_state.current = user  # as agent_server.handle sets it
                before = len(record)
                result = await agent.ask(steered, reset=True)
                await asyncio.sleep(2.5)
                calls = [c for c in record[before:] if "tool" in c]
                trace["turns"].append(
                    {
                        "user": user,
                        "calls": calls,
                        "reply": result.get("text", ""),
                        "chain_phase": phase,
                        "scratch_entries": pad.render().count("\n## "),
                    }
                )
                last_turn = trace["turns"][-1]
                history += [
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": result.get("text", "")},
                ]
                if (
                    scenario.overlap
                    and turn != scenario.turns[-1]
                    and any(c["tool"] == "delegate" for c in calls)
                ):
                    continue  # the follow-up is spoken while this job still runs
                if any(c["tool"] == "delegate" for c in calls) and chain.data["job_id"]:
                    job = await _wait_job(state, chain.data["job_id"])
                    trace["job"] = job
                    server.note_chain_job(chain.data["job_id"])
                    history.append({"role": "assistant", "content": str(job.get("summary", ""))})
                last_turn["chain_items"] = list(chain.data["items"])
            if scenario.overlap:
                trace["job"] = await _wait_all(state)
        finally:
            await agent.stop()
        _collect_files(trace, workspace)
        rows = _job_rows(state)
        if scenario.overlap or rows:
            trace["jobs"] = rows
        trace["scratch"] = {"exists": pad.path.exists(), "text": pad.render()}
        usage = {k: agent.tap.usage[k] - spent0.get(k, 0) for k in agent.tap.usage}
        job_usd = sum(float(r.get("usd", 0.0)) for r in rows) or float(
            (trace["job"] or {}).get("usd", 0.0)
        )
        trace["spend"] = (usage["prompt"] * 0.25 + usage["completion"] * 1.5) / 1e6 + job_usd
        trace["lint_flags"] = (
            0 if not str((trace["job"] or {}).get("summary", "")).startswith(PREFIXES) else 1
        )
        return trace

    return runner


def _capture_urls(
    inner: Callable[..., Awaitable[None]], sink: list[str]
) -> Callable[..., Awaitable[None]]:
    async def handler(params: Any) -> None:
        deliver = params.result_callback

        async def seen(result: Any, *, properties: Any = None) -> None:
            sink.extend(_urls(str(result)))
            await deliver(result, properties=properties)

        params.result_callback = seen
        await inner(params)

    return handler


def _job_rows(state: Path) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in json.loads((state / "jobs.json").read_text())["jobs"]]
    except (OSError, ValueError, KeyError):
        return []


async def _wait_all(state: Path) -> dict[str, Any]:
    """Until no job runs (a queued follow-up starts as its parent ends, so look twice); the last
    job's row, which is the one that carries the finished deliverable."""
    deadline = time.monotonic() + JOB_WAIT_S
    quiet = 0
    while time.monotonic() < deadline:
        rows = _job_rows(state)
        quiet = quiet + 1 if rows and all(r["status"] != "running" for r in rows) else 0
        if quiet >= 2:
            return rows[-1]
        await asyncio.sleep(3.0)
    return {"status": "timeout", "summary": "", "usd": 0.0}


async def _wait_job(state: Path, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + JOB_WAIT_S
    while time.monotonic() < deadline:
        try:
            rows = json.loads((state / "jobs.json").read_text())["jobs"]
            row = next(r for r in rows if r["id"] == job_id)
            if row["status"] != "running":
                return dict(row)
        except (OSError, ValueError, KeyError, StopIteration):
            pass
        await asyncio.sleep(2.0)
    return {"id": job_id, "status": "timeout", "summary": "", "usd": 0.0}


def _collect_files(trace: Trace, workspace: Path) -> None:
    sources = workspace / "SOURCES.json"
    scratch_dir = workspace / "scratch"
    trace["files"]["SOURCES.json"] = sources.read_text() if sources.exists() else None
    trace["files"]["text_deliverables"] = sorted(
        str(p.relative_to(workspace))
        for p in workspace.rglob("*")
        if p.is_file()
        and p.suffix in (".html", ".htm", ".md", ".txt", ".csv")
        and p != sources
        and not p.is_relative_to(scratch_dir)
    )
    apps_dir = workspace / "apps"
    trace["files"]["text"] = " ".join(
        (workspace / name).read_text(errors="ignore")
        for name in trace["files"]["text_deliverables"]
    )
    trace["files"]["apps"] = (
        sorted(str(p.relative_to(apps_dir)) for p in apps_dir.rglob("*") if p.is_file())
        if apps_dir.exists()
        else []
    )


# --- run + report ---------------------------------------------------------------------------


async def evaluate(
    scenarios: Sequence[Scenario],
    trials: int,
    runner: Runner,
    budget: Budget,
    seed: int = 0,
    sink: Path | None = None,
    seeds: int = 1,
) -> tuple[list[dict[str, Any]], bool]:
    """(one row per scenario-trial, whether the budget stopped the run early). Seeds seed ..
    seed+seeds-1 each run `trials` trials, numbered one after another."""
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        for index in range(trials * seeds):
            trial = index
            if not budget.allows():
                return rows, True
            params = pick_params(scenario, index % trials, seed + index // trials)
            trace = await runner(scenario, params, trial)
            budget.add(float(trace.get("spend", 0.0)))
            results = score(scenario, trace)
            row = {
                "id": scenario.id,
                "trial": trial,
                "params": params,
                "results": results,
                "pass": trial_passes(results),
                "spend": trace.get("spend", 0.0),
                "workspace": trace.get("workspace"),
                "trace": trace,
            }
            rows.append(row)
            if sink is not None:
                with sink.open("a") as out:
                    out.write(json.dumps(row) + "\n")
    return rows, False


def table(rows: Sequence[dict[str, Any]], spent: float) -> str:
    lines = [f"{'scenario':<28}{'trials':>7}{'pass^k':>8}  predicates (pass/fail/n.a. over trials)"]
    for sid in dict.fromkeys(r["id"] for r in rows):
        mine = [r for r in rows if r["id"] == sid]
        names = list(dict.fromkeys(n for r in mine for n in r["results"]))
        cells = []
        for name in names:
            vals = [r["results"].get(name) for r in mine]
            cells.append(f"{name} {vals.count(True)}/{vals.count(False)}/{vals.count(None)}")
        ok = all(r["pass"] for r in mine)
        lines.append(f"{sid:<28}{len(mine):>7}{'yes' if ok else 'NO':>8}  " + "; ".join(cells))
    lines.append(f"total spend ${spent:.3f}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--list", action="store_true", help="print the scenarios and the estimate")
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--max-usd", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--seeds", type=int, default=1, help="run seeds seed..seed+N-1, each as separate trials"
    )
    parser.add_argument("--dry", action="store_true", help="fakes: no network, no cost")
    parser.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    scenarios = load_scenarios(args.scenarios)
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s.id in wanted]
    cost = estimate(scenarios, args.trials * args.seeds)
    if args.list:
        for s in scenarios:
            print(f"{s.id:<28}{s.kind:<15}{len(s.turns)} turns  expects {', '.join(s.expect)}")
        print(
            f"estimate: about ${cost:.2f} for {len(scenarios)} scenarios x "
            f"{args.trials * args.seeds} trial(s)"
        )
        return 0
    run_dir = args.out / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.max_usd, args.out / ("ledger-dry.json" if args.dry else "ledger.json"))
    print(f"estimate: about ${cost:.2f}; spent so far ${budget.spent:.3f} of ${args.max_usd:.2f}")
    if not args.dry and cost >= STOP_AT * args.max_usd - budget.spent:
        print("not started: the estimate does not fit under the stop line (raise --max-usd)")
        return 3
    runner = dry_runner if args.dry else make_live_runner(run_dir, args.seed)
    rows, stopped = asyncio.run(
        evaluate(
            scenarios, args.trials, runner, budget, args.seed, run_dir / "trials.jsonl", args.seeds
        )
    )
    print(table(rows, budget.spent))
    print(f"traces: {run_dir / 'trials.jsonl'}")
    for row in rows:
        if row["workspace"]:
            print(f"workspace {row['id']}-{row['trial']}: {row['workspace']}")
    if stopped:
        print("stopped: 90 % of the budget reached; the rows so far are kept")
    return 0 if rows and all(r["pass"] for r in rows) and not stopped else 1


if __name__ == "__main__":
    raise SystemExit(main())
