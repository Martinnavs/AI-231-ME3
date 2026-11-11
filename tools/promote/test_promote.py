"""tools/promote/promote.py, against real throwaway git repositories.

.venv/bin/python -m pytest -q tools/promote
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import promote  # noqa: E402

RULES = """\
- .scratch/**
+ src/**
- src/skip_me.py
+ docs/A.md
+ docs/INDEX.md
"""
INDEX = """# Documentation index

| File | What |
|---|---|
| `INDEX.md` | this |
| `A.md` | in the tree |
| `B.md` | not in the tree |
"""


def sh(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    base = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Dev",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "Dev",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, **base, **(env or {})},
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit(
    repo: Path, message: str, files: dict[str, str | None], author: str = "Dev", date: str = ""
) -> str:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    sh(repo, "add", "-A")
    env = {"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com"}
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    sh(repo, "commit", "-q", "-m", message, env=env)
    return sh(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    commit(
        r,
        "base",
        {
            "src/a.py": "a = 1\n",
            ".scratch/old.txt": "old\n",
            "docs/INDEX.md": INDEX,
            "docs/A.md": "A\n",
        },
    )
    sh(r, "checkout", "-q", "-b", "dev")
    monkeypatch.chdir(r)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    for k, v in {
        "GIT_AUTHOR_NAME": "Tool",
        "GIT_AUTHOR_EMAIL": "tool@example.com",
        "GIT_COMMITTER_NAME": "Tool",
        "GIT_COMMITTER_EMAIL": "tool@example.com",
    }.items():
        monkeypatch.setenv(k, v)
    return r


def rules() -> list[promote.Rule]:
    return promote.parse_rules(RULES)


def build(branch: str = "prod", onto: str = "main", **kw: object) -> int:
    return promote.cmd_build(
        "dev",
        onto,
        branch,
        rules(),
        bool(kw.get("dry", False)),
        ["Co-Authored-By: T <t@example.com>"],
        bool(kw.get("replace", False)),
    )


def history(repo: Path, ref: str, onto: str = "main") -> list[str]:
    return sh(repo, "log", "--reverse", "--format=%an|%s", f"{onto}..{ref}").splitlines()


def files(repo: Path, ref: str) -> set[str]:
    return set(sh(repo, "ls-tree", "-r", "--name-only", ref).splitlines())


def test_rules_last_match_wins_and_star_stays_in_one_folder() -> None:
    r = promote.parse_rules("+ src/**\n- src/x.py\n+ src/x.py\n+ top/*.md\n")
    assert promote.promoted("src/deep/er/m.py", r) and promote.promoted("src/x.py", r)
    assert promote.promoted("top/a.md", r) and not promote.promoted("top/sub/a.md", r)
    assert promote.decision("other/f", r) is None
    assert promote.decision("src/x.py", promote.parse_rules("+ src/**\n- src/x.py\n")) is False


def test_a_bad_rules_line_is_an_error() -> None:
    with pytest.raises(promote.PromoteError):
        promote.parse_rules("src/**\n")


def test_filter_index_drops_rows_for_missing_docs_and_adds_the_banner_once() -> None:
    once = promote.filter_index(INDEX, {"docs/A.md", "docs/INDEX.md"})
    assert "`A.md`" in once and "`B.md`" not in once and "`INDEX.md`" in once
    assert once.count("This is the production tree") == 1
    assert promote.filter_index(once, {"docs/A.md", "docs/INDEX.md"}) == once


def test_only_promotable_changes_are_replayed_with_author_message_and_trailer(repo: Path) -> None:
    commit(
        repo,
        "add b and some research",
        {"src/b.py": "b\n", ".scratch/n.txt": "n\n"},
        author="Alice",
        date="2026-01-01T10:00:00+00:00",
    )
    commit(repo, "research only", {".scratch/m.txt": "m\n"})
    commit(
        repo,
        "edit a, drop b, add an excluded file",
        {"src/a.py": "a = 2\n", "src/b.py": None, "src/skip_me.py": "x\n"},
        author="Bob",
        date="2026-01-02T10:00:00+00:00",
    )
    commit(repo, "docs", {"docs/B.md": "B\n", "docs/INDEX.md": INDEX + "| `A.md` | again |\n"})
    assert build() == 0
    assert history(repo, "prod") == [
        "Alice|add b and some research",
        "Bob|edit a, drop b, add an excluded file",
        "Dev|docs",  # its INDEX.md change is promotable; docs/B.md is not
        "Tool|docs: index lists only the docs in the production tree",
    ]
    tree = files(repo, "prod")
    assert "src/a.py" in tree and ".scratch/old.txt" in tree  # what main had stays
    assert "src/b.py" not in tree and "src/skip_me.py" not in tree
    assert ".scratch/n.txt" not in tree and "docs/B.md" not in tree
    assert sh(repo, "show", "prod:src/a.py") == "a = 2"
    assert "`B.md`" not in sh(repo, "show", "prod:docs/INDEX.md")
    body = sh(repo, "log", "--format=%B", "-3", "prod")
    assert body.count("Promoted-from: ") == 3 and "Co-Authored-By: T" in body
    date = sh(repo, "log", "--reverse", "--format=%aI", "main..prod").splitlines()[0]
    assert date.startswith("2026-01-01T10:00:00")
    assert promote.cmd_verify("prod", "dev", rules(), "main") == 0


def test_a_merge_commit_is_replayed_as_one_step_from_its_first_parent(repo: Path) -> None:
    sh(repo, "checkout", "-q", "-b", "side")
    commit(repo, "side work", {"src/c.py": "c\n"})
    sh(repo, "checkout", "-q", "dev")
    commit(repo, "dev work", {"src/d.py": "d\n"})
    sh(repo, "merge", "--no-ff", "-q", "-m", "merge side", "side")
    assert build() == 0
    assert history(repo, "prod")[:2] == ["Dev|dev work", "Dev|merge side"]
    assert {"src/c.py", "src/d.py"} <= files(repo, "prod")


def test_a_dry_run_creates_nothing_and_an_existing_branch_needs_replace(repo: Path) -> None:
    commit(repo, "x", {"src/x.py": "x\n"})
    assert build(dry=True) == 0
    assert sh(repo, "branch", "--list", "prod") == ""
    assert build() == 0
    with pytest.raises(promote.PromoteError, match="exists"):
        build()
    assert build(replace=True) == 0


def test_the_second_promotion_replays_only_what_is_new(repo: Path) -> None:
    commit(repo, "first", {"src/one.py": "1\n"})
    assert build() == 0
    sh(repo, "branch", "-f", "main", "prod")  # the owner merges the promotion into main
    commit(repo, "research", {".scratch/z.txt": "z\n"})
    commit(repo, "second", {"src/two.py": "2\n"})
    assert build(branch="prod2") == 0
    assert history(repo, "prod2", onto="prod") == ["Dev|second"]
    assert promote.cmd_verify("prod2", "dev", rules(), "main") == 0


def test_main_changing_a_promoted_file_stops_the_next_promotion(repo: Path) -> None:
    commit(repo, "first", {"src/one.py": "1\n"})
    assert build() == 0
    sh(repo, "branch", "-f", "main", "prod")
    sh(repo, "checkout", "-q", "main")
    commit(repo, "hotfix on main", {"src/one.py": "hotfix\n"})
    sh(repo, "checkout", "-q", "dev")
    commit(repo, "more", {"src/two.py": "2\n"})
    with pytest.raises(promote.PromoteError, match="main changed promoted files"):
        build(branch="prod2")


def test_a_non_promoted_change_on_main_does_not_stop_it(repo: Path) -> None:
    commit(repo, "first", {"src/one.py": "1\n"})
    assert build() == 0
    sh(repo, "branch", "-f", "main", "prod")
    sh(repo, "checkout", "-q", "main")
    commit(repo, "scratch note on main", {".scratch/main_only.txt": "m\n"})
    sh(repo, "checkout", "-q", "dev")
    commit(repo, "more", {"src/two.py": "2\n"})
    assert build(branch="prod2") == 0
    assert ".scratch/main_only.txt" not in files(repo, "dev")


def test_the_first_promotion_needs_main_inside_the_source_branch(repo: Path) -> None:
    sh(repo, "checkout", "-q", "main")
    commit(repo, "main moved on its own", {"other.txt": "o\n"})
    sh(repo, "checkout", "-q", "dev")
    with pytest.raises(promote.PromoteError, match="not an ancestor"):
        build()


def test_verify_catches_a_wrong_or_extra_file(repo: Path) -> None:
    commit(repo, "x", {"src/x.py": "x\n"})
    assert build() == 0
    sh(repo, "checkout", "-q", "prod")
    commit(repo, "tamper", {"src/x.py": "tampered\n", "stray.txt": "s\n"})
    sh(repo, "checkout", "-q", "dev")
    assert promote.cmd_verify("prod", "dev", rules(), "main") == 1


def test_check_flags_new_files_in_production_folders_that_no_rule_decides(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, "new", {"src/new.py": "n\n", "scripts/mystery.py": "m\n", "notes/x.txt": "x\n"})
    assert promote.cmd_check("dev", rules(), strict=False) == 0
    out = capsys.readouterr().out
    assert "scripts/mystery.py" in out and "src/new.py" not in out and "notes/x.txt" not in out
    assert promote.cmd_check("dev", rules(), strict=True) == 1
