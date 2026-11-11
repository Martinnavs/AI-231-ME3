"""What a voice should say for addresses, file names, initials and markdown (live, 2026-11-11)."""

import pytest

from lan_speech.speakable import speakable


@pytest.mark.parametrize(
    "written, said",
    [
        ("See coe.upd.edu.ph today", "See coe dot upd dot edu dot ph today"),
        ("at https://www.upd.edu.ph/academics now", "at upd dot edu dot ph now"),
        ("Open email_to_upd_professor.md", "Open email to upd professor markdown file"),
        ("It is in primes.txt and sieve.py", "It is in primes text file and sieve python file"),
        ("The U.P. A.I. program", "The U P A I program"),
        ("an AI program", "an A I program"),
        ("a Ph.D. track", "a P H D track"),
        ("UP Diliman and UPD", "U P Diliman and U P D"),
        ("speed up the page", "speed up the page"),  # the word, not the school
        ("e.g. a table, i.e. this", "for example a table, that is this"),
        ("Pi is 3.14 and costs 4.99. Done.", "Pi is 3.14 and costs 4.99. Done."),
        ("Dr. Eugene Jalao and Prof. Cruz", "Doctor Eugene Jalao and Professor Cruz"),
        ("Mr. Cruz, Mrs. Cruz and Ms. Cruz", "Mister Cruz, Missus Cruz and Miss Cruz"),
        ("cats vs. dogs, apples, etc.", "cats versus dogs, apples, et cetera"),
        ("a sentence ending in ph. Next", "a sentence ending in ph. Next"),
    ],
)
def test_speakable(written: str, said: str) -> None:
    assert speakable(written) == said


def test_markdown_marks_are_not_spoken() -> None:
    md = "# Email Draft\n**To:** Dr. Jalao\n1. first\n- second\nuse `code` here"
    out = speakable(md)
    assert "#" not in out and "*" not in out and "`" not in out
    assert "Email Draft" in out and "To: Doctor Jalao" in out and "first" in out
