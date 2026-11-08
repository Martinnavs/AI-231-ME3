"""scripts/chain_eval.py: predicates on canned traces, the budget stop, the estimate, --dry."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "chain_eval", Path(__file__).resolve().parents[1] / "scripts" / "chain_eval.py"
)
assert _spec and _spec.loader
ce = importlib.util.module_from_spec(_spec)
sys.modules["chain_eval"] = ce
_spec.loader.exec_module(ce)

URL = "https://example.org/a"


def turn(
    user: str, *calls: tuple[str, dict[str, Any]], phase: str = "idle", reply: str = ""
) -> Any:
    return {
        "user": user,
        "calls": [{"tool": t, "args": a} for t, a in calls],
        "reply": reply,
        "chain_phase": phase,
    }


def good() -> dict[str, Any]:
    return {
        "turns": [
            turn("What is the latest thing?", ("web_research", {"query": "q", "recent": True})),
            turn(
                "Build a page.",
                ("delegate", {"task": f"Use the verified facts, source {URL}"}),
                phase="researching",
            ),
        ],
        "files": {
            "SOURCES.json": json.dumps([{"url": URL, "fact": "f"}]),
            "text_deliverables": ["a.md"],
        },
        "web_urls": [URL],
        "job": {"summary": "Built it."},
        "lint_flags": 0,
    }


def test_scenarios_cover_different_kinds() -> None:
    scenarios = ce.load_scenarios()
    assert len(scenarios) >= 7
    assert {s.kind for s in scenarios} >= {"research_build", "code_only", "plain", "followup"}
    assert {s.deliverable for s in scenarios} >= {"html", "md", "py", "game"}
    for s in scenarios:
        assert set(s.expect) <= set(ce.PREDICATES)
        for name in s.params:
            assert any("{" + name + "}" in t for t in s.turns)


def test_params_are_random_but_reproducible() -> None:
    s = next(x for x in ce.load_scenarios() if x.id == "event_invitation")
    assert ce.pick_params(s, 0, 1) == ce.pick_params(s, 0, 1)
    assert len({ce.pick_params(s, t, 1)["org"] for t in range(12)}) > 1
    assert "{" not in ce.fill(s.turns[0], ce.pick_params(s, 0, 1))


def test_recency_tool() -> None:
    assert ce.recency_tool(good()) is True
    bad = good()
    bad["turns"][0]["calls"][0]["args"]["recent"] = False
    assert ce.recency_tool(bad) is False
    plain = {"turns": [turn("Who wrote it?")]}
    assert ce.recency_tool(plain) is None


def test_max_lookups_per_turn() -> None:
    assert ce.max_lookups_per_turn(good())
    two = good()
    two["turns"][0]["calls"].append({"tool": "web_answer", "args": {}})
    assert not ce.max_lookups_per_turn(two) and ce.max_lookups_per_turn(two, 2)


def test_chain_not_idle_before_build_and_task_content() -> None:
    assert ce.chain_not_idle_before_build(good()) is True
    bad = good()
    bad["turns"][1]["chain_phase"] = "idle"
    assert ce.chain_not_idle_before_build(bad) is False
    assert ce.delegate_task_has_facts_or_url(good()) is True
    bare = good()
    bare["turns"][1]["calls"][0]["args"]["task"] = "Make a page about it."
    assert ce.delegate_task_has_facts_or_url(bare) is False
    bare["turns"][1]["calls"][0]["args"]["task"] = (
        "Make it. Notes and sources from the conversation are in /sandbox/scratch/NOTES.md: read it"
    )
    assert ce.delegate_task_has_facts_or_url(bare) is True
    bare["turns"][1]["calls"][0]["args"]["task"] = "Make it from these details: x"
    assert ce.delegate_task_has_facts_or_url(bare) is True
    assert ce.delegate_task_has_facts_or_url({"turns": [turn("hi")]}) is None


def test_deliverable_sources() -> None:
    assert ce.deliverable_sources(good()) is True
    missing = good()
    missing["files"]["SOURCES.json"] = None
    assert ce.deliverable_sources(missing) is False
    invented = good()
    invented["files"]["SOURCES.json"] = json.dumps([{"url": "https://nowhere.example/x"}])
    assert ce.deliverable_sources(invented) is False
    none = good()
    none["files"]["text_deliverables"] = []
    assert ce.deliverable_sources(none) is None


def test_lint_clean_accepts_zero_flags_or_an_honest_prefix() -> None:
    assert ce.lint_clean(good()) is True
    flagged = good()
    flagged["lint_flags"] = 2
    assert ce.lint_clean(flagged) is False
    flagged["job"]["summary"] = "Unverified content: Built it."
    assert ce.lint_clean(flagged) is True


def test_code_only_plain_and_followup_predicates() -> None:
    code = good()
    code["files"] = {"SOURCES.json": None, "text_deliverables": []}
    assert ce.no_gate_for_code_only(code)
    code["job"]["summary"] = "Unsourced: x"
    assert not ce.no_gate_for_code_only(code)
    assert not ce.no_web_for_plain(good())
    assert ce.no_web_for_plain({"turns": [turn("make a game", ("delegate", {"task": "t"}))]})
    follow = {"turns": [turn("who wrote x", ("web_answer", {})), turn("when was he born")]}
    assert ce.no_chain_for_followup(follow)
    follow["turns"][1]["chain_phase"] = "researching"
    assert not ce.no_chain_for_followup(follow)
    assert ce.asked_or_flagged({"turns": [turn("x", reply="I'm assuming Y.")]})


def test_score_uses_only_declared_predicates_and_report_only_never_fails() -> None:
    s = ce.Scenario("s", "k", "md", True, [], {}, {"recency_tool": True, "asked_or_flagged": True})
    results = ce.score(s, good())
    assert set(results) == {"recency_tool", "asked_or_flagged"}
    assert results["asked_or_flagged"] is False and ce.trial_passes(results)
    results["recency_tool"] = False
    assert not ce.trial_passes(results)


def test_estimate_and_budget_stop(tmp_path: Path) -> None:
    scenarios = ce.load_scenarios()[:3]
    assert ce.estimate(scenarios, 2) == pytest.approx(0.12)
    budget = ce.Budget(0.05, tmp_path / "l.json")
    assert budget.allows()
    budget.add(0.03)
    assert not budget.allows()  # 0.03 + 0.02 reaches 90 % of 0.05
    assert json.loads((tmp_path / "l.json").read_text())["usd"] == pytest.approx(0.03)
    assert ce.Budget(0.05, tmp_path / "l.json").spent == pytest.approx(0.03)


def test_the_run_stops_at_the_budget_and_keeps_its_rows(tmp_path: Path) -> None:
    async def runner(s: Any, p: Any, t: int) -> Any:
        trace = copy.deepcopy(good())
        trace.update(id=s.id, trial=t, params=p, spend=0.02, workspace=None)
        return trace

    scenarios = ce.load_scenarios()[:3]
    budget = ce.Budget(0.1, tmp_path / "l.json")
    rows, stopped = asyncio.run(
        ce.evaluate(scenarios, 2, runner, budget, sink=tmp_path / "t.jsonl")
    )
    assert stopped and len(rows) == 4  # 0.02 * 4 = 0.08 < 0.09; the fifth would cross
    assert len((tmp_path / "t.jsonl").read_text().splitlines()) == 4


def test_dry_run_end_to_end_passes_every_scenario(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = ce.main(["--dry", "--trials", "2", "--out", str(tmp_path), "--run-id", "t"])
    out = capsys.readouterr().out
    assert code == 0 and "total spend $0.000" in out and "pass^k" in out
    rows = [json.loads(line) for line in (tmp_path / "t" / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 16 and all(r["pass"] for r in rows)


def test_only_and_list_do_not_run_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ce.main(["--list", "--only", "snake_game,pronoun_followup", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "snake_game" in out and "pronoun_followup" in out and "estimate: about $0.04" in out
    assert not list(tmp_path.iterdir())


def test_a_live_run_that_does_not_fit_the_budget_does_not_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ce.main(["--max-usd", "0.05", "--out", str(tmp_path)]) == 3
    assert "not started" in capsys.readouterr().out


def test_recency_tool_accepts_a_grounded_web_answer_but_not_no_lookup() -> None:
    answer = {"turns": [turn("What is the current rate?", ("web_answer", {"query": "rate"}))]}
    assert ce.recency_tool(answer) is True
    none = {"turns": [turn("What is the current rate?")]}
    assert ce.recency_tool(none) is False
    stale = {"turns": [turn("latest?", ("web_research", {"query": "q", "recent": False}))]}
    assert ce.recency_tool(stale) is False


def test_cached_repeats_do_not_count_as_lookups() -> None:
    t = {
        "turns": [
            turn(
                "q",
                ("web_research", {"query": "a"}),
                ("web_answer", {"query": "a"}),
            )
        ]
    }
    assert not ce.max_lookups_per_turn(t)
    t["turns"][0]["calls"][1]["cached"] = True
    assert ce.max_lookups_per_turn(t)


def test_deliverable_sources_new_rules() -> None:
    other = good()
    other["web_urls"] = []
    assert ce.deliverable_sources(other) is False  # url unseen, summary names no sources
    other["scratch"] = {"exists": True, "text": f"## x\n{URL}"}
    assert ce.deliverable_sources(other) is True  # seen in the scratchpad
    other["scratch"] = {}
    other["job"]["summary"] = "Built it from these sources: one."
    assert ce.deliverable_sources(other) is True  # the job names its sources
    bad = good()
    bad["files"]["SOURCES.json"] = json.dumps([{"url": "web_answer: when is it", "fact": "f"}])
    bad["job"]["summary"] = "sources named"
    assert ce.deliverable_sources(bad) is False  # non-http url always fails
    empty = good()
    empty["files"]["SOURCES.json"] = "[]"
    assert ce.deliverable_sources(empty) is False


def test_scratchpad_has_findings() -> None:
    t = good()
    t["turns"][0]["calls"][0]["args"]["query"] = "what is the latest thing"
    t["scratch"] = {"exists": True, "text": "## 00:00 web_research: what is the latest thing\nx"}
    assert ce.scratchpad_has_findings(t) is True
    t["scratch"] = {"exists": True, "text": "## 00:00 web_research: something else"}
    assert ce.scratchpad_has_findings(t) is False
    t["scratch"] = {"exists": False, "text": ""}
    assert ce.scratchpad_has_findings(t) is False
    assert ce.scratchpad_has_findings({"turns": [turn("hi")]}) is None


def test_delegate_reads_notes() -> None:
    t = good()
    t["turns"][1]["scratch_entries"] = 2
    assert ce.delegate_reads_notes(t) is False
    t["turns"][1]["calls"][0]["args"]["task"] += " read /sandbox/scratch/NOTES.md first"
    assert ce.delegate_reads_notes(t) is True
    t["turns"][1]["scratch_entries"] = 0
    assert ce.delegate_reads_notes(t) is None


def test_research_scenarios_declare_the_new_predicates_and_others_do_not() -> None:
    for s in ce.load_scenarios():
        has = "scratchpad_has_findings" in s.expect and "delegate_reads_notes" in s.expect
        assert has == (s.kind == "research_build")


def test_asked_or_flagged_regex() -> None:
    for reply in ("Which one?", "Do you mean A?", "I'm assuming A.", "assumption: B"):
        assert ce.asked_or_flagged({"turns": [turn("x", reply=reply)]})
    assert not ce.asked_or_flagged({"turns": [turn("x", reply="Done.")]})


def test_seeds_run_as_separate_trials_with_different_parameters(tmp_path: Path) -> None:
    seen: list[tuple[int, dict[str, str]]] = []

    async def runner(s: Any, p: Any, t: int) -> Any:
        seen.append((t, p))
        trace = copy.deepcopy(good())
        trace.update(id=s.id, trial=t, params=p, spend=0.0, workspace=None)
        return trace

    scenario = next(x for x in ce.load_scenarios() if x.id == "event_invitation")
    budget = ce.Budget(10.0, tmp_path / "l.json")
    rows, _ = asyncio.run(ce.evaluate([scenario], 1, runner, budget, seed=3, seeds=4))
    assert [t for t, _ in seen] == [0, 1, 2, 3] and len(rows) == 4
    assert len({p["org"] for _, p in seen}) > 1


def test_main_seeds_multiply_the_estimate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ce.main(["--list", "--only", "snake_game", "--seeds", "3", "--out", str(tmp_path)])
    assert "estimate: about $0.06" in capsys.readouterr().out
