"""The data generator: labels parse, nothing leaks from the evaluation sets, splits are disjoint, runs repeat."""

import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "data"))
sys.path.insert(0, str(HERE / "spec"))
import generate as G  # noqa: E402
import tools as T  # noqa: E402

ROUTER_NAMES = {t.name for t in T.TOOLS if t.group in ("home", "info", "pi", "control") or t.name == "search_docs"} - {
    "search_docs"
}


def run(out: Path, n: int = 600, seed: int = 0) -> dict[str, list[dict]]:
    subprocess.run(
        [sys.executable, str(HERE / "data/generate.py"), "--out", str(out), "--n", str(n), "--seed", str(seed)],
        check=True,
        capture_output=True,
    )
    return {s: [json.loads(x) for x in (out / f"{s}.jsonl").read_text().splitlines()] for s in ("train", "dev", "test")}


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return run(tmp_path_factory.mktemp("gen"))


def test_every_target_parses_and_stays_in_the_routers_domain(data):
    for rows in data.values():
        for r in rows:
            for ln in r["target"].splitlines():
                assert T.parse_line(ln).name in ROUTER_NAMES, ln  # never an OnIt tool: those are deferred


def test_the_assistant_message_is_the_target_and_the_system_message_has_the_phonebook(data):
    r = data["train"][0]
    assert r["messages"][-1] == {"role": "assistant", "content": r["target"]}
    assert r["messages"][0]["role"] == "system" and "Phonebook:" in r["messages"][0]["content"]


def test_contact_targets_name_someone_in_the_phonebook(data):
    for rows in data.values():
        for r in rows:
            if r["kind"] == "contact":
                book = r["messages"][0]["content"]
                assert T.parse_line(r["target"]).args[0] in book


def test_no_near_duplicate_of_an_evaluation_prompt(data):
    eval_g = [(t, G.grams(t)) for t in G.eval_prompts()]
    for rows in data.values():
        for r in rows:
            assert not G.leaks(r["messages"][-2]["content"], eval_g), r["messages"][-2]["content"]


def test_splits_share_no_identical_example(data):
    seen = {}
    for split, rows in data.items():
        for r in rows:
            sig = (r["messages"][-2]["content"], json.dumps(r["messages"][1:-2]), r["target"])
            assert seen.setdefault(sig, split) == split


def test_all_kinds_are_present_and_deferrals_use_the_known_reasons(data):
    kinds = {r["kind"] for r in data["train"]}
    assert {"home", "info", "contact", "defer", "follow-up", "multi-call", "chat", "ask"} <= kinds
    reasons = {T.parse_line(r["target"]).args[0] for r in data["train"] if r["kind"] == "defer"}
    assert reasons == {"tools", "code", "workflow", "reasoning", "other"}


def test_the_same_seed_gives_the_same_files(tmp_path):
    run(tmp_path / "a", n=200, seed=3)
    run(tmp_path / "b", n=200, seed=3)
    digest = lambda p: hashlib.sha256((p / "train.jsonl").read_bytes()).hexdigest()  # noqa: E731
    assert digest(tmp_path / "a") == digest(tmp_path / "b")


def test_noise_never_empties_an_utterance():
    rng = random.Random(0)
    for _ in range(200):
        assert G.add_noise("Turn on the lights, please!", rng).strip()


def test_alarm_wording_never_changes_the_canonical_target():
    rng = random.Random(1)
    for _ in range(50):
        e = G.s_slot(rng)
        for ln in e["target"].splitlines():
            call = T.parse_line(ln)
            if call.name == "ALARM":
                assert call.args[0].count(":") == 1 and call.args[0].endswith(("AM", "PM"))


def test_web_answer_targets_are_complete_questions_and_the_boundary_has_negatives(data):
    web = [r for rows in data.values() for r in rows if r["target"].startswith("web_answer|")]
    assert web, "no web_answer examples"
    for r in web:
        query = T.parse_line(r["target"]).args[0]
        assert query.endswith("?") and len(query.split()) >= 4, query
    # the deferral banks no longer hold single current-fact items
    deferred = {r["messages"][-2]["content"] for rows in data.values() for r in rows if r["kind"] == "defer"}
    assert not any("typhoons today" in u or "price of rice" in u for u in deferred)


def test_now_and_today_do_not_turn_weather_time_or_home_into_a_lookup():
    rng = random.Random(2)
    for _ in range(300):
        e = G.s_now_variants(rng)
        assert not e["target"].startswith(("web_answer", "escalate")), (e["user"], e["target"])
        assert any(w in e["user"] for w in ("now", "today", "moment", "instant", "later")), e["user"]


def test_settled_facts_stay_a_topic_lookup():
    rng = random.Random(3)
    for _ in range(100):
        e = G.s_settled(rng)
        assert T.parse_line(e["target"]).name == "wiki_summary"


def test_web_answer_has_a_weight_comparable_to_wiki():
    weights = {m.__name__: w for w, m in G.MIX}
    assert 0.5 <= weights["s_web_answer"] / weights["s_wiki"] <= 2


def test_arithmetic_is_reasoning_never_a_home_action():
    rng = random.Random(4)
    users = set()
    for _ in range(400):
        e = G.s_arithmetic(rng)
        assert e["target"] == "escalate|reasoning", (e["user"], e["target"])
        users.add(e["user"])
    assert len(users) > 150  # digits, number words and many operations


def test_arithmetic_and_pi_status_in_the_generated_data(data):
    arith = [r for rows in data.values() for r in rows if r["kind"] == "arith"]
    assert arith and all(T.parse_line(r["target"]).name == "escalate" for r in arith)
    for rows in data.values():
        for r in rows:
            for ln in r["target"].splitlines():
                if T.parse_line(ln).name == "TIMER":
                    assert any(u in ln for u in ("second", "minute", "hour")), ln  # a duration, never a bare sum


def test_pi_temperature_is_device_status_not_a_lookup():
    rng = random.Random(5)
    seen = set()
    for _ in range(400):
        e = G.s_pi(rng)
        if e["target"] == "device_status":
            seen.add(e["user"])
    assert "what's the pi temperature" in seen and len(seen) >= 8
