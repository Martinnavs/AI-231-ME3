"""scripts/search_eval.py: the cases, the scoring and the dry run."""

import asyncio
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "search_eval", Path(__file__).resolve().parents[1] / "scripts" / "search_eval.py"
)
assert spec and spec.loader
se = importlib.util.module_from_spec(spec)
sys.modules["search_eval"] = se
spec.loader.exec_module(se)


def test_the_cases_span_recent_old_local_and_global() -> None:
    cases = se.load_cases()
    words = {w for c in cases for w in c.span.split()}
    assert {"recent", "old", "local", "global"} <= words
    assert all(c.must and c.question and c.keywords for c in cases)
    assert len({c.id for c in cases}) == len(cases)


def test_a_result_passes_only_when_every_required_word_is_in_it() -> None:
    assert se.passes("Eugene Rex Jalao leads it", ["jalao"])
    assert not se.passes("founded in 1908", ["Bartlett", "1908"])
    assert se.passes("version 3.14.2", ["3.15|3.14"])  # either of two spellings


def test_the_dry_run_passes_every_case_for_both_tools_and_keywords_change_the_query() -> None:
    seen: list[str] = []

    async def call(tool: str, args: dict[str, str]) -> str:
        seen.append(args["query"])
        return await se.dry_call(tool, args)

    cases = se.load_cases()
    rows = asyncio.run(se.run(cases, ["web_answer", "web_research"], call))
    assert len(rows) == 2 * len(cases) and all(r["pass"] for r in rows)
    seen.clear()
    asyncio.run(se.run(cases[:1], ["web_answer"], call, keywords=True))
    assert seen == [cases[0].keywords]
    assert se.main(["--dry"]) == 0


def test_the_live_search_eval_does_not_start_past_the_tavily_line(
    monkeypatch: object, capsys: object
) -> None:
    import voice_agent.tavily_budget as budget

    monkeypatch.setattr(budget, "credit_status", lambda: (900, 1000, "reserve"))  # type: ignore[attr-defined]
    assert se.main(["--tool", "research"]) == 4
    assert "not started" in capsys.readouterr().out  # type: ignore[attr-defined]
