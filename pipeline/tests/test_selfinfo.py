"""Answers about the owner from config/profile.md: local, no model, no links."""

from pathlib import Path

import pytest

from voice_pipeline import fast_path, selfinfo

PROFILE = """# Profile
<!-- a comment - Name: Ignored -->
## Basics
- Name: Ada Lovelace
- Pronouns: She/Her
- Occupation: Analyst
- Employer: Analytical Engines Ltd
- Degree Program: Mathematics
- School: University of London
- City: London
## Links (public only)
- GitHub: https://github.com/ada
- Email (public one only): ada@example.com
- Academic Article: [Notes on the Engine](https://example.com/notes)
## Course Information
- Current Course Name: Analysis 101
- Instructor Name: Dr. Babbage
## Demo Information
- Demo School Name: Some Other School
- Name: A second name is ignored
"""


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path) -> None:
    file = tmp_path / "profile.md"
    file.write_text(PROFILE)
    selfinfo.load(file)
    yield
    selfinfo.FIELDS.clear()


def test_parse_keeps_the_first_of_a_label_drops_comments_and_urls() -> None:
    fields = selfinfo.parse(PROFILE)
    assert fields["name"] == "Ada Lovelace" and fields["demo school name"] == "Some Other School"
    assert "github" not in fields and "email (public one only)" not in fields  # urls and mail
    assert fields["academic article"] == "Notes on the Engine"  # a link keeps its text


@pytest.mark.parametrize(
    ("what", "said"),
    [
        ("name", "Your name is Ada Lovelace."),
        ("work", "You work as an Analyst at Analytical Engines Ltd."),
        ("study", "Your degree program is Mathematics, at University of London."),
        ("city", "You're based in London."),
        ("course", "You're taking Analysis 101 with Dr. Babbage."),
        (
            "about",
            "You're Ada Lovelace. You work as an Analyst at Analytical Engines Ltd. "
            "Your degree program is Mathematics, at University of London.",
        ),
    ],
)
def test_each_kind_of_question_is_answered_from_the_fields(what: str, said: str) -> None:
    assert selfinfo.answer(what) == said


def test_the_school_is_not_the_demo_school_and_a_missing_field_gives_no_answer(
    tmp_path: Path,
) -> None:
    only = tmp_path / "p.md"
    only.write_text("- Demo School Name: Elsewhere\n- Name: Ada\n")
    selfinfo.load(only)
    assert selfinfo.answer("study") is None and selfinfo.answer("work") is None
    assert selfinfo.answer("name") == "Your name is Ada." and selfinfo.answer("nonsense") is None
    assert selfinfo.load(tmp_path / "missing.md") == 0 and selfinfo.answer("name") is None


@pytest.mark.parametrize(
    ("heard", "what"),
    [
        ("What's my name?", "self:name"),
        ("Can you tell me my name?", "self:name"),
        ("Where do I work?", "self:work"),
        ("What am I studying?", "self:study"),
        ("Where do I live?", "self:city"),
        ("Who is my instructor?", "self:course"),
        ("Who am I?", "self:about"),
        ("Tell me about myself", "self:about"),
    ],
)
def test_the_fast_path_answers_questions_about_the_owner(heard: str, what: str) -> None:
    found = fast_path.match(heard)
    assert found is not None and found.tool == "clock" and found.args["what"] == what
    assert fast_path.local_answer(found, None).strip() != ""  # type: ignore[arg-type]


def test_without_a_profile_the_turn_goes_on_as_before() -> None:
    selfinfo.FIELDS.clear()
    assert fast_path.match("What's my name?") is None
    assert fast_path.match("Tell me about myself") is None


def test_other_requests_about_me_are_not_taken_by_the_fast_path() -> None:
    for said in (
        "Make me a page about me",
        "What is my name doing in the file",
        "Build my website",
    ):
        assert fast_path.match(said) is None


def test_owner_name_is_the_profile_name_and_none_without_a_profile() -> None:
    assert selfinfo.owner_name() == "Ada Lovelace"
    selfinfo.FIELDS.clear()
    assert selfinfo.owner_name() is None
