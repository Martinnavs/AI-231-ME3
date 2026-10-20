"""Names pulled from documents for the speech server's name list."""

from __future__ import annotations

from pathlib import Path

from doc_chat.names import extract_names, names_from_folder

TEXT = """# Hogwarts
Albus Dumbledore was the headmaster of Hogwarts. Dumbledore trusted Hagrid, and Hagrid loved
Fluffy. The Order of the Phoenix met in secret. Students walked to Hogwarts every morning.
However, Harry, Ron and Hermione went to the Ministry of Magic. In Hogwarts the Order of the
Phoenix was famous. We knew Dumbledore's wand was long. Fluffy barked at Hagrid.
"""


def test_runs_of_capitalised_words_are_names() -> None:
    names = extract_names(TEXT)
    assert "Albus Dumbledore" in names
    assert "Order of the Phoenix" in names
    assert names["Order of the Phoenix"] == 2
    assert "Ministry of Magic" in names


def test_a_possessive_is_the_same_name() -> None:
    assert extract_names(TEXT)["Dumbledore"] >= 2


def test_commas_separate_names() -> None:
    names = extract_names(TEXT, min_count=1)
    assert {"Harry", "Ron"} <= set(names)
    assert not any("Harry Ron" in n or "Hermione and" in n for n in names)


def test_the_joins_only_after_of() -> None:
    names = extract_names("In Hogwarts the Order of the Phoenix met.", min_count=1)
    assert "Order of the Phoenix" in names and "Hogwarts" in names
    assert not any("Hogwarts the" in n for n in names)


def test_sentence_initial_ordinary_words_are_not_names() -> None:
    names = extract_names(TEXT, min_count=1)
    assert not ({"However", "Students", "In", "The"} & set(names))


def test_a_single_word_needs_to_repeat() -> None:
    names = extract_names("Zorp lives here. We met Gandalf. Gandalf is wise.", min_count=2)
    assert "Gandalf" in names and "Zorp" not in names


def test_a_word_only_ever_capitalised_at_a_sentence_start_is_not_a_name() -> None:
    names = extract_names("Chamber doors opened. Chamber music played. It was dark.", min_count=2)
    assert "Chamber" not in names


def test_a_folder_gives_the_most_frequent_names_first(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(TEXT)
    (tmp_path / "b.txt").write_text("Hagrid said hello to Hagrid. Fluffy wagged.")
    (tmp_path / "ignore.pdf").write_text("Ignored Ignored Ignored")
    names = names_from_folder(tmp_path)
    assert names[0] in {"Hagrid", "Dumbledore", "Hogwarts"}
    assert "Ignored" not in names
