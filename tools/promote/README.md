# Promoting production code from the dev branch to `main`

`main` holds only what runs the agent (code, the data it reads, tests, build and ops config, operating docs). Research, experiments, recorded results and history stay on the development branch `agy/interrupts`. This tool moves the production part across, repeatedly, with a replayed history.

**Never merge `agy/interrupts` into `main` directly**: it would add every experiment. Promote instead.

## The loop

```bash
python tools/promote/promote.py check                 # every tracked file has a decision? new files are listed
python tools/promote/promote.py build --source agy/interrupts --onto origin/main \
       --branch prod/merge-YYYY-MM-DD --trailer "Co-Authored-By: ..."   # NEW local branch; --dry-run first
python tools/promote/promote.py verify --branch prod/merge-YYYY-MM-DD --source agy/interrupts --onto origin/main
.venv/bin/python -m pytest -q tools/promote           # the tool's own tests
```

Then check out the branch in a scratch directory and run the full gates there, and only then fast-forward `main` (a human step; the tool never touches `main` or any remote).

## What `build` does

For each first-parent commit of the source that is not yet promoted it copies the blobs of the files `rules.txt` promotes onto the tip of `--onto`, keeping author, dates and message and adding a `Promoted-from: <sha>` trailer. Commits with nothing promotable are skipped. A last commit filters `docs/INDEX.md` to the docs that are in the tree (and adds a banner). The next run starts after the newest `Promoted-from` trailer on `--onto`.

It **refuses** when: the branch name exists (`--replace-branch` rebuilds it); `--onto` changed a promoted file since the last promotion (a hotfix on `main` would be overwritten: bring it back to the dev branch first); the first promotion's `--onto` is not inside the source.

## `rules.txt`

`+ glob` promotes, `- glob` excludes, the last matching line wins, a path no line decides is **not** promoted (an allowlist). `**` crosses folders, `*` does not. `check` lists files in production folders (`src/ pipeline/ scripts/ tests/ config/ deploy/ assets/ corpus/ docs/` and the repo root) that no line decides: add a `+` or `-` for each, deliberately. Whole-folder `+` lines exist only where every tracked file in the folder is production, so a new file in a mixed folder is always flagged.

`analyze.py` is the reachability cross-check that produced the first rules: seeds are the production Makefile targets, `scripts/stack/*.sh`, `deploy/` and the QA/setup tools; it follows imports (including `python -m pkg` -> `pkg/__main__.py`), module names and file paths in string literals, and writes `out/promote/manifest.tsv`. Run it when unsure whether a new file is production. What it taught (2026-11-11): the router loads two files under `finetune/` by path; `tests` name fixture folders dynamically (`reply-{n}.wav`); config files are named without their folder; scripts are imported by bare name after a `sys.path` insert. Overlaying only the promoted files on `main` and running both test suites is the check that catches anything it misses.

## Caveats

- Promotion rewrites hashes: `main`'s commits are copies, not the dev branch's. Keep the dev branch as the archive; tag it (`archive/agy-interrupts-YYYY-MM-DD`) before the first promotion.
- A mixed commit keeps its original message but carries fewer files; the message can mention work that is not on `main`.
- Docs are promoted by name (see `rules.txt`); the research docs are only on the dev branch, which `INDEX.md`'s banner says.
- Merging `main` back into the dev branch is safe (same content for promoted paths).
