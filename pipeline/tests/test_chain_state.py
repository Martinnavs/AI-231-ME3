import json
from pathlib import Path

import pytest

from voice_pipeline.chain_state import ChainState, is_chain_tool


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_new_state_is_idle_and_has_no_block(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    assert not chain.active() and chain.block() == ""


def test_update_persists_and_a_restart_reloads_it(tmp_path: Path) -> None:
    clock = Clock()
    chain = ChainState(tmp_path, clock)
    chain.update("awaiting_fact_approval", subject="Ana Cruz", facts=["a" * 300, "b", "c", "d"])
    assert chain.data["facts"] == ["a" * 160, "b", "c"]
    clock.now += 30
    again = ChainState(tmp_path, clock)
    assert again.active() and again.data["phase"] == "awaiting_fact_approval"
    assert again.data["subject"] == "Ana Cruz"


def test_the_file_is_written_atomically(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("building", job_id="job7")
    assert json.loads((tmp_path / "chain.json").read_text())["job_id"] == "job7"
    assert [p.name for p in tmp_path.iterdir()] == ["chain.json"]  # no temp file left


def test_unknown_phase_or_field_is_refused(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    with pytest.raises(ValueError):
        chain.update("nonsense")
    with pytest.raises(ValueError):
        chain.update("building", updated_at=1)


def test_expires_after_ten_minutes_and_on_reload(tmp_path: Path) -> None:
    clock = Clock()
    chain = ChainState(tmp_path, clock)
    chain.update("building")
    clock.now += 599
    assert chain.active()
    clock.now += 2
    assert not chain.active() and chain.block() == ""
    chain.update("building")
    clock.now += 700
    assert not ChainState(tmp_path, clock).active()


def test_three_unrelated_turns_expire_and_an_update_resets_the_count(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("awaiting_name")
    chain.touch_unrelated()
    chain.touch_unrelated()
    assert chain.active()
    chain.update("awaiting_name", subject="x")
    chain.touch_unrelated()
    chain.touch_unrelated()
    assert chain.active()
    chain.touch_unrelated()
    assert not chain.active()


def test_idle_phase_clears_everything(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("building", subject="s", job_id="j")
    chain.update("idle")
    assert not chain.active() and chain.data["subject"] == ""


def test_block_format_is_one_to_three_lines(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("awaiting_fact_approval", subject="Ana Cruz", facts=["f1", "f2"], job_id="job3")
    assert chain.block() == (
        '[ACTIVE TASK: phase=awaiting_fact_approval | subject="Ana Cruz" | '
        'facts="f1 ; f2" | job=job3]'
    )
    chain.update("building", artifact_path="apps/ana/index.html")
    lines = chain.block().splitlines()
    assert 1 <= len(lines) <= 3 and lines[1] == "[ARTIFACT: apps/ana/index.html]"


def test_a_damaged_file_is_idle(tmp_path: Path) -> None:
    (tmp_path / "chain.json").write_text("{nope")
    assert not ChainState(tmp_path).active()


def test_chain_tools() -> None:
    for name in ("chain_update", "web_research", "web_answer", "delegate", "delegate_status"):
        assert is_chain_tool(name)
    assert not is_chain_tool("weather")


def test_researching_is_active_and_facts_add_up_without_duplicates(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("researching", subject="MIT event", facts=["Hunt is in January", "Other"])
    assert chain.active()
    chain.update("researching", facts=["hunt is in january", "x" * 300, "Third"])
    assert chain.data["facts"] == ["Other", "hunt is in january", "x" * 160, "Third"]


def test_research_facts_are_capped_at_eight_and_carry_into_the_next_phase(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("researching", facts=[f"fact {i}" for i in range(6)])
    chain.update("researching", facts=[f"more {i}" for i in range(6)])
    assert len(chain.data["facts"]) == 8 and chain.data["facts"][-1] == "more 5"
    chain.update("awaiting_fact_approval", facts=["approved one"])
    assert len(chain.data["facts"]) == 8 and chain.data["facts"][-1] == "approved one"
    chain.update(
        "awaiting_fact_approval", facts=["a", "b", "c", "d", "e"]
    )  # other phases: as before
    assert chain.data["facts"] == ["a", "b", "c"]


def test_the_block_with_eight_long_facts_stays_under_900_characters(tmp_path: Path) -> None:
    chain = ChainState(tmp_path)
    chain.update("researching", subject="s" * 80, facts=[f"{i}" + "y" * 200 for i in range(8)])
    chain.update("researching", job_id="job12", artifact_path="apps/some_site/index.html")
    block = chain.block()
    assert len(block) < 900 and block.startswith("[ACTIVE TASK: phase=researching")
    assert len(block.splitlines()) <= 3
