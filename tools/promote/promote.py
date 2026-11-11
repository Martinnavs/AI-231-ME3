"""Promote the production files of the dev branch to main, as a replayed history.

    python tools/promote/promote.py check  [--ref HEAD]
    python tools/promote/promote.py build  --source agy/interrupts --onto origin/main \\
                                           --branch prod/merge-YYYY-MM-DD [--dry-run]
    python tools/promote/promote.py verify --branch prod/merge-YYYY-MM-DD --source agy/interrupts

What it does: for every first-parent commit of `source` that is not yet promoted, it applies only
the changes to files that `rules.txt` promotes, on top of `onto`, keeping the author, dates and
message (plus a `Promoted-from: <sha>` trailer). Commits with nothing promotable are skipped. The
result is a
NEW local branch; `main`, the remote and the source branch are never touched. A second run starts
after the last `Promoted-from` trailer found on `onto`, so promotion repeats as development goes on.

Safety: it copies blobs (no patching, no merging), refuses to run if `onto` changed a promoted file
since the last promotion (it would overwrite it), and `verify` proves that every promoted file of
the source equals the branch's and that nothing else was added. A final commit filters
docs/INDEX.md to the docs that are present. See tools/promote/README.md.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RULES = Path(__file__).with_name("rules.txt")
TRAILER = "Promoted-from: "
ZERO = "0" * 40
PRODUCTION_FOLDERS = (
    "src/",
    "pipeline/",
    "scripts/",
    "tests/",
    "config/",
    "deploy/",
    "assets/",
    "corpus/",
)
INDEX = "docs/INDEX.md"


class PromoteError(Exception):
    pass


def repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if out.returncode:
        raise PromoteError("not inside a git repository")
    return Path(out.stdout.strip())


def git(
    *args: str,
    input: str | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    check: bool = True,
) -> str:
    done = subprocess.run(
        ["git", *args],
        input=input,
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
    )
    if check and done.returncode:
        raise PromoteError(f"git {' '.join(args[:3])} failed: {done.stderr.strip()[:300]}")
    return done.stdout


# ---- rules -------------------------------------------------------------------------------
def glob_to_re(glob: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**", i):
            out += ".*"
            i += 2
        elif glob[i] == "*":
            out += "[^/]*"
            i += 1
        elif glob[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(glob[i])
            i += 1
    return re.compile(out + r"\Z")


Rule = tuple[bool, re.Pattern[str]]


def parse_rules(text: str) -> list[Rule]:
    rules: list[Rule] = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sign, _, glob = line.partition(" ")
        if sign not in ("+", "-") or not glob.strip():
            raise PromoteError(f"rules line {n}: expected '+ glob' or '- glob', got {line!r}")
        rules.append((sign == "+", glob_to_re(glob.strip())))
    return rules


def decision(path: str, rules: list[Rule]) -> bool | None:
    """True promote, False exclude, None when no rule decides (the last matching rule wins)."""
    result: bool | None = None
    for yes, pattern in rules:
        if pattern.match(path):
            result = yes
    return result


def promoted(path: str, rules: list[Rule]) -> bool:
    return decision(path, rules) is True


# ---- docs index --------------------------------------------------------------------------
BANNER = (
    "> **This is the production tree.** Research notes, history, experiments and recorded results "
    "live on the development branch `agy/interrupts` (`docs/`, `.scratch/`, `finetune/`); a doc "
    "named below that is not in `docs/` is there.\n"
)
ROW = re.compile(r"^\|\s*`([A-Za-z0-9_.-]+\.md)`\s*\|")


def filter_index(text: str, present: set[str]) -> str:
    """Drop table rows that name a docs/*.md file which is not in the tree; add the banner once."""
    kept = []
    for line in text.splitlines():
        m = ROW.match(line)
        if m and f"docs/{m.group(1)}" not in present and (m.group(1) != "INDEX.md"):
            continue
        kept.append(line)
    out = "\n".join(kept) + "\n"
    if BANNER.strip() not in out:
        head, _, rest = out.partition("\n")
        out = f"{head}\n\n{BANNER}{rest}"
    return out


# ---- reading the repository --------------------------------------------------------------
def ls_tree(ref: str) -> dict[str, tuple[str, str]]:
    """path -> (mode, blob) for every file at `ref`."""
    out: dict[str, tuple[str, str]] = {}
    for rec in git("ls-tree", "-r", "-z", ref).split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "blob":
            out[path] = (mode, sha)
    return out


def is_ancestor(old: str, new: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", old, new]).returncode == 0


def last_promoted(onto: str) -> tuple[str, str] | None:
    """(source sha, the commit on `onto` that carried it) of the newest promotion, or None."""
    log = git("log", "-z", "--format=%H%x1f%B", "--grep", f"^{TRAILER}", onto, check=False)
    for rec in log.split("\0"):
        if not rec.strip():
            continue
        sha, _, body = rec.partition("\x1f")
        m = re.search(rf"^{re.escape(TRAILER)}([0-9a-f]{{40}})\s*$", body, re.M)
        if m:
            return m.group(1), sha.strip()
    return None


# ---- check -------------------------------------------------------------------------------
def cmd_check(ref: str, rules: list[Rule], strict: bool) -> int:
    files = sorted(ls_tree(ref))
    yes = [f for f in files if promoted(f, rules)]
    no = [f for f in files if decision(f, rules) is False]
    undecided = [f for f in files if decision(f, rules) is None]
    flagged = [f for f in undecided if f.startswith(PRODUCTION_FOLDERS) or "/" not in f]
    print(
        f"{ref}: {len(files)} tracked files | promoted {len(yes)} | excluded by a '-' rule "
        f"{len(no)} | not promoted (no rule) {len(undecided) - len(flagged)} | undecided in "
        f"production folders {len(flagged)}"
    )
    if flagged:
        print("\nTracked files in production folders that no rule decides (add '+' or '-'):")
        for f in flagged:
            print("  ", f)
    return 1 if strict and flagged else 0


# ---- build -------------------------------------------------------------------------------
def commit_meta(sha: str) -> tuple[dict[str, str], str]:
    fmt = "%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI"
    an, ae, ad, cn, ce, cd = git("show", "-s", f"--format={fmt}", sha).strip().split("\x1f")
    env = {
        "GIT_AUTHOR_NAME": an,
        "GIT_AUTHOR_EMAIL": ae,
        "GIT_AUTHOR_DATE": ad,
        "GIT_COMMITTER_NAME": cn,
        "GIT_COMMITTER_EMAIL": ce,
        "GIT_COMMITTER_DATE": cd,
    }
    return env, git("show", "-s", "--format=%B", sha).rstrip()


def changes(parent: str | None, commit: str) -> list[tuple[str, str, str, str]]:
    """(status, new mode, new blob, path) for each file changed against the first parent."""
    base = parent or git("hash-object", "-t", "tree", "/dev/null").strip()
    out = git("diff-tree", "-r", "-z", "--no-renames", "--raw", base, commit)
    parts = out.split("\0")
    result = []
    i = 0
    while i < len(parts) - 1:
        meta = parts[i]
        if not meta.startswith(":"):
            i += 1
            continue
        _old_mode, new_mode, _old, new, status = meta[1:].split()[:5]
        result.append((status[0], new_mode, new, parts[i + 1]))
        i += 2
    return result


def apply_to_tree(tree: str, edits: list[tuple[str, str, str]], scratch: Path) -> str:
    """The tree after `edits` (path, mode, blob; blob ZERO = delete), through a temporary index."""
    index = str(scratch / "index")
    if os.path.exists(index):
        os.unlink(index)
    env = {"GIT_INDEX_FILE": index}
    git("read-tree", tree, env=env)
    lines = [
        f"0 {ZERO}\t{path}\n" if blob == ZERO else f"{mode} {blob}\t{path}\n"
        for path, mode, blob in edits
    ]
    git("update-index", "--index-info", input="".join(lines), env=env)
    return git("write-tree", env=env).strip()


def cmd_build(
    source: str,
    onto: str,
    branch: str,
    rules: list[Rule],
    dry: bool,
    trailers: list[str],
    replace_branch: bool,
) -> int:
    source_sha = git("rev-parse", "--verify", f"{source}^{{commit}}").strip()
    onto_sha = git("rev-parse", "--verify", f"{onto}^{{commit}}").strip()
    previous = last_promoted(onto_sha)
    if previous:
        since, carried_on = previous
        if not is_ancestor(since, source_sha):
            raise PromoteError(f"last promoted commit {since[:10]} is not an ancestor of {source}")
        touched = set(git("diff", "--name-only", carried_on, onto_sha).split("\n")) - {""}
        clash = sorted(p for p in touched if promoted(p, rules))
        if clash:
            raise PromoteError(
                "main changed promoted files since the last promotion (they would be overwritten): "
                + ", ".join(clash[:8])
            )
    else:
        if not is_ancestor(onto_sha, source_sha):
            raise PromoteError(
                f"{onto} is not an ancestor of {source}: first promotion needs main "
                "to be contained in the source branch"
            )
        since = onto_sha
    if (
        not replace_branch
        and not dry
        and git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).strip()
    ):
        raise PromoteError(f"branch {branch} exists (use --replace-branch to rebuild it)")
    commits = git("rev-list", "--reverse", "--first-parent", f"{since}..{source_sha}").split()
    cur_commit = onto_sha
    cur_tree = git("rev-parse", f"{onto_sha}^{{tree}}").strip()
    made = skipped = 0
    with tempfile.TemporaryDirectory(prefix="promote-") as tmp:
        scratch = Path(tmp)
        for c in commits:
            parent = git("rev-parse", "-q", "--verify", f"{c}^1", check=False).strip() or None
            edits = []
            for status, mode, blob, path in changes(parent, c):
                if not promoted(path, rules) or mode == "160000":
                    continue
                edits.append((path, mode, ZERO if status == "D" else blob))
            if not edits:
                skipped += 1
                continue
            new_tree = apply_to_tree(cur_tree, edits, scratch)
            if new_tree == cur_tree:
                skipped += 1
                continue
            env, message = commit_meta(c)
            message = f"{message}\n\n{TRAILER}{c}"
            cur_commit = git(
                "commit-tree", new_tree, "-p", cur_commit, input=message + "\n", env=env
            ).strip()
            cur_tree = new_tree
            made += 1
        # docs/INDEX.md lists only docs that are present
        files = ls_tree(cur_tree)
        if INDEX in files:
            old = git("cat-file", "blob", files[INDEX][1])
            new = filter_index(old, set(files))
            if new != old:
                blob = git("hash-object", "-w", "--stdin", input=new).strip()
                new_tree = apply_to_tree(cur_tree, [(INDEX, files[INDEX][0], blob)], scratch)
                # the trailer marks the tool's last commit: the guard and the next run start here
                msg = "docs: index lists only the docs in the production tree\n\n" + "\n".join(
                    [*trailers, f"{TRAILER}{commits[-1]}"]
                )
                cur_commit = git(
                    "commit-tree", new_tree, "-p", cur_commit, input=msg.rstrip() + "\n"
                ).strip()
                cur_tree = new_tree
                made += 1
    print(
        f"source {source} @ {source_sha[:10]} onto {onto} @ {onto_sha[:10]} "
        f"(replaying after {since[:10]})"
    )
    print(
        f"first-parent commits considered: {len(commits)} | created: {made} | "
        f"skipped (nothing promotable): {skipped}"
    )
    if dry:
        print("dry run: no branch created")
        return 0
    git("update-ref", f"refs/heads/{branch}", cur_commit)
    print(f"branch {branch} -> {cur_commit[:10]} (tree {cur_tree[:10]})")
    return cmd_verify(branch, source, rules, onto)


# ---- verify ------------------------------------------------------------------------------
def cmd_verify(branch: str, source: str, rules: list[Rule], onto: str | None = None) -> int:
    built = ls_tree(branch)
    src = ls_tree(source)
    want = {p: v for p, v in src.items() if promoted(p, rules)}
    problems = []
    for path, (mode, blob) in want.items():
        if path == INDEX:
            continue  # filtered on purpose
        if built.get(path) != (mode, blob):
            problems.append(f"differs or missing: {path}")
    if onto:
        base = ls_tree(onto)
        for path in built:
            if path not in base and not promoted(path, rules):
                problems.append(f"not promotable but added: {path}")
    print(
        f"verify {branch}: {len(want)} promoted files in {source}, {len(built)} in the branch, "
        f"{len(problems)} problem(s)"
    )
    for p in problems[:20]:
        print("  ", p)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--ref", default="HEAD")
    p.add_argument("--strict", action="store_true")
    p = sub.add_parser("build")
    p.add_argument("--source", required=True)
    p.add_argument("--onto", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--replace-branch", action="store_true")
    p.add_argument("--trailer", action="append", default=[], help="added to the index commit")
    p = sub.add_parser("verify")
    p.add_argument("--branch", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--onto")
    ap.add_argument("--rules", type=Path, default=RULES)
    args = ap.parse_args(argv)
    try:
        os.chdir(repo_root())
        rules = parse_rules(args.rules.read_text())
        if args.cmd == "check":
            return cmd_check(args.ref, rules, args.strict)
        if args.cmd == "build":
            return cmd_build(
                args.source,
                args.onto,
                args.branch,
                rules,
                args.dry_run,
                args.trailer,
                args.replace_branch,
            )
        return cmd_verify(args.branch, args.source, rules, args.onto)
    except PromoteError as exc:
        print(f"promote: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
