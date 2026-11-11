"""Reading a text deliverable back (live, 2026-11-11: "read it" started a delegate job, and "open"
on a markdown file was refused with a rule about app names)."""

from pathlib import Path

from voice_pipeline.agent_tools import READ_CHARS, open_app, read_deliverable, readable_files


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "NOTES.md").write_text("private notes")
    (tmp_path / "scratch" / "PROFILE.md").write_text("profile")
    (tmp_path / "email_to_upd_professor.md").write_text("# Email Draft\n\nDear Dr. Jalao,\nHello.")
    (tmp_path / "SOURCES.json").write_text("[]")
    (tmp_path / "apps" / "snake").mkdir(parents=True)
    (tmp_path / "apps" / "snake" / "index.html").write_text("<html>")
    (tmp_path / "script.py").write_text("print(1)")
    return tmp_path


def test_only_text_deliverables_are_readable_never_notes_profile_or_code(tmp_path: Path) -> None:
    names = {p.name for p in readable_files(_workspace(tmp_path))}
    assert names == {"email_to_upd_professor.md"}


def test_a_file_is_found_by_name_or_by_how_it_is_said(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    assert "Dear Dr. Jalao" in read_deliverable("email_to_upd_professor.md", root=root)
    assert "Dear Dr. Jalao" in read_deliverable("Email To UPD Professor.md", root=root)
    assert "can't find" in read_deliverable("nothing.md", root=root)
    assert "email_to_upd_professor.md" in read_deliverable("", root=root)


def test_the_name_cannot_escape_the_workspace_or_reach_notes(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    for bad in ("../../etc/passwd", "scratch/NOTES.md", "NOTES.md", "/etc/hosts"):
        assert "private notes" not in read_deliverable(bad, root=root)
        assert "can't find" in read_deliverable(bad, root=root)


def test_a_long_file_comes_in_parts_with_the_next_offset(tmp_path: Path) -> None:
    (tmp_path / "long.txt").write_text("word " * 1000)
    first = read_deliverable("long.txt", root=tmp_path)
    assert f"offset={READ_CHARS}" in first
    last = read_deliverable("long.txt", offset=READ_CHARS * 3, root=tmp_path)
    assert "More follows" not in last


def test_open_app_on_a_file_name_says_it_is_a_file_and_offers_to_read(tmp_path: Path) -> None:
    said, url = open_app("email_to_upd_professor.md", "open it", root=tmp_path)
    assert url is None and "file" in said and "read" in said and "App names" not in said
