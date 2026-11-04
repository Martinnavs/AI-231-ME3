"""scripts/make_entities.py: merging document names into a general names file."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_entities.py"


@pytest.fixture
def me(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("make_entities", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "make_entities", mod)
    spec.loader.exec_module(mod)
    return mod


def test_merge_keeps_base_categories_and_adds_new_names_as_given(me) -> None:  # type: ignore[no-untyped-def]
    base = {"Cebu": "place", "EDSA": "term"}
    out = me.merge_names(base, ["Suttree", "cebu", "edsa", "Knoxville"], "doc")
    assert out == {"Cebu": "place", "EDSA": "term", "Suttree": "doc", "Knoxville": "doc"}
    assert base == {"Cebu": "place", "EDSA": "term"}


def test_common_single_words_are_dropped_but_phrases_kept(me) -> None:  # type: ignore[no-untyped-def]
    known = frozenset({"god", "road"})
    names = ["God", "Child of God", "Suttree", "Road"]
    assert me.without_common_words(names, known) == ["Child of God", "Suttree"]


def test_main_merges_a_corpus_with_a_names_file(me, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("Suttree walked. Suttree sat. Cornelius Suttree left. " * 3)
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"EDSA": "term"}))
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        sys, "argv", ["make_entities", str(docs), "-o", str(out), "--merge", str(base)]
    )
    assert me.main() == 0
    merged = json.loads(out.read_text())
    assert merged["EDSA"] == "term" and merged["Cornelius Suttree"] == "doc"
