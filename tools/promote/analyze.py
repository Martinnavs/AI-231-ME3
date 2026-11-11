"""Reachability cross-check for the promotion rules (tools/promote/README.md).

Which files changed on agy/interrupts (against origin/main) does the running agent need?

Read-only. Seeds: the production Makefile targets and scripts/stack/*.sh (what `make up`, `talk`,
`down`, `status`, `calibrate`, `doctor-tools`, `pi-*` run), plus the systemd units under deploy/.
Closure: Python imports, dotted module names in string literals, `-m module` and script paths in
shell, and repository paths named in string literals (the data the code reads).
Output: .scratch/prod-merge/manifest.tsv (path, git status, bucket, evidence) and a summary.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "promote"  # git-ignored
OUT.mkdir(parents=True, exist_ok=True)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


tracked = set(git("ls-tree", "-r", "--name-only", "HEAD").split("\n")) - {""}
changed: dict[str, str] = {}
for line in git("diff", "--name-status", "origin/main...HEAD").splitlines():
    parts = line.split("\t")
    changed[parts[-1]] = parts[0][0]

PROD_TARGETS = {
    "pi-detect",
    "up",
    "up-laptop",
    "up-pi",
    "restart-agent",
    "down",
    "status",
    "talk",
    "calibrate",
    "doctor-tools",
    "pi-check",
    "pi-env",
    "pi-firewall",
    "pi-tune",
    "pi-netem",
    "spotify-login",
}
# ---- python module map -------------------------------------------------------------------
MODROOTS = [ROOT / "src", ROOT / "pipeline" / "src"]
modfile: dict[str, str] = {}
for base in MODROOTS:
    for p in base.rglob("*.py"):
        rel = p.relative_to(base).with_suffix("")
        name = ".".join(rel.parts[:-1] if rel.parts[-1] == "__init__" else rel.parts)
        modfile[name] = p.relative_to(ROOT).as_posix()
for p in (ROOT / "scripts").glob("*.py"):
    modfile[f"scripts.{p.stem}"] = p.relative_to(ROOT).as_posix()
for _p in (ROOT / "scripts").glob("*.py"):
    modfile.setdefault(_p.stem, _p.relative_to(ROOT).as_posix())  # `from pi_device_mcp import ...`
file_mod = {
    v: k
    for k, v in modfile.items()
    if k.startswith("scripts.") or "." not in k or v.startswith(("src/", "pipeline/src/"))
}
DOTTED = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")


def resolve_module(name: str) -> list[str]:
    """Files for a dotted module name and its package prefixes."""
    out = []
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        f = modfile.get(".".join(parts[:i]))
        if f:
            out.append(f)
    return out


def py_edges(path: str) -> tuple[set[str], set[str]]:
    """(repo files imported or named by module string, string constants)."""
    try:
        tree = ast.parse((ROOT / path).read_text(errors="ignore"))
    except SyntaxError:
        return set(), set()
    mod = file_mod.get(path, "")
    pkg = mod.rsplit(".", 1)[0] if "." in mod and not path.endswith("__init__.py") else mod
    files: set[str] = set()
    consts: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                files.update(resolve_module(a.name))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = pkg.split(".")[: len(pkg.split(".")) - (node.level - 1)] if pkg else []
                base = ".".join([*up, *([base] if base else [])])
            files.update(resolve_module(base))
            for a in node.names:
                files.update(resolve_module(f"{base}.{a.name}"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            consts.add(v)
            if v.endswith(".py") and f"scripts/{v}" in tracked:
                files.add(f"scripts/{v}")  # a test that loads a script by path
            if DOTTED.match(v):
                files.update(resolve_module(v))
    return files, consts


# ---- shell / make ------------------------------------------------------------------------
SH_MOD = re.compile(r"-m\s+([A-Za-z_][\w.]*)")
SH_PATH = re.compile(r"((?:[\w.~-]+/)*[\w.-]+\.(?:py|sh|json|toml|txt|service|conf|env|md))")


def sh_edges(text: str) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    consts: set[str] = set()
    for m in SH_MOD.finditer(text):
        name = m.group(1)
        files.update(resolve_module(name))
        files.update(resolve_module(f"{name}.__main__"))  # `python -m pkg` runs pkg/__main__.py
    for m in SH_PATH.finditer(text):
        p = m.group(1).lstrip("./")
        consts.add(p)
        for cand in (p, f"scripts/{p}", f"scripts/stack/{p}", f"deploy/{p}"):
            if cand in tracked:
                files.add(cand)
    return files, consts


make_text = (ROOT / "Makefile").read_text()
targets: dict[str, str] = {}
cur = None
for line in make_text.splitlines():
    m = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
    if m:
        cur = m.group(1)
        targets[cur] = line + "\n"
    elif cur and line.startswith("\t"):
        targets[cur] += line + "\n"
    elif line and not line.startswith(("#", " ")):
        cur = None


DATA_ROOTS = ("config/", "pipeline/config/", "assets/", "deploy/")
data_by_name: dict[str, list[str]] = defaultdict(list)
for _t in tracked:
    if _t.startswith(DATA_ROOTS) and "." in Path(_t).name:
        data_by_name[Path(_t).name].append(_t)


def closure(seeds: set[str]) -> dict[str, str]:
    """file -> the file that first reached it (evidence)."""
    seen: dict[str, str] = {s: "(seed)" for s in seeds if s in tracked}
    queue = list(seen)
    while queue:
        f = queue.pop()
        p = ROOT / f
        if not p.is_file():
            continue
        if f.endswith(".py"):
            files, consts = py_edges(f)
        elif f.endswith((".sh", ".service", ".md", ".txt", ".toml", ".conf")) or p.suffix == "":
            if f.endswith(".md") or f.endswith(".txt") and not f.startswith("deploy/"):
                continue
            files, consts = sh_edges(p.read_text(errors="ignore"))
        else:
            continue
        for c in consts:
            if len(c) < 4 or c.startswith(("http", "~", "/", "-")):
                continue
            if "/" not in c and len(data_by_name.get(c, [])) == 1:
                files.add(data_by_name[c][0])  # a data file named without its folder
            for t in tracked:
                if t.startswith((".scratch/", "docs/", "resources/")):
                    continue
                if t == c or ("/" in c and "." in c.rsplit("/", 1)[-1] and t.endswith("/" + c)):
                    files.add(t)
                elif c.startswith(("corpus/", "config/", "assets/", "models/")) and t.startswith(
                    c.rstrip("/") + "/"
                ):
                    files.add(t)  # a data directory the code reads
        for g in files:
            if g in tracked and g not in seen:
                seen[g] = f
                queue.append(g)
    return seen


prod_seed_files: set[str] = set()
for t in PROD_TARGETS:
    if t in targets:
        files, _ = sh_edges(targets[t])
        prod_seed_files.update(files)
prod_seed_files.update(
    f
    for f in tracked
    if f.startswith("scripts/stack/")
    and f.endswith(".sh")
    and Path(f).name not in {"fdbench_client.sh", "pi_clips.sh"}
)
prod_seed_files.update(f for f in tracked if f.startswith("deploy/"))
prod_seed_files.update(
    {
        "scripts/calibrate.sh",
        "scripts/headset_mic.sh",
        "scripts/delegate_doctor.py",
        "scripts/pi_device_mcp.py",
        "scripts/pi_speaker.py",
        "scripts/spotify_login.py",
    }
)
D2_TOOLS = {  # the owner's D2 choice: QA harness and setup helpers the Makefile/README point to
    "scripts/chain_eval.py",
    "scripts/addressee_replay.py",
    "scripts/make_pi_clips.py",
    "scripts/stack/pi_clips.sh",
    "pipeline/src/voice_pipeline/dryrun.py",
    "pipeline/src/voice_pipeline/search_count.py",
    "config/eval/chain_scenarios.jsonl",
    "scripts/fetch_laptop_models.py",
    "scripts/fetch_turn_model.py",
    "scripts/make_entities.py",
    "scripts/power_mode.sh",
}
prod_seed_files |= {f for f in D2_TOOLS if f in tracked}
prod = closure(prod_seed_files)

OPS_DOCS = [
    "README.md",
    "pipeline/README.md",
    "deploy/PI-HARNESS-COMMANDS.md",
    "docs/ARCHITECTURE.md",
    "docs/PI-REMOTE-SPEAKER.md",
    "docs/AUDIO-IO-AND-AEC.md",
    "docs/MCP-SERVERS.md",
]
ops_ref: dict[str, str] = {}
for d in OPS_DOCS:
    if d in tracked:
        _, consts = sh_edges((ROOT / d).read_text(errors="ignore"))
        for c in consts:
            for cand in (c, f"scripts/{c}", f"config/{c}", f"deploy/{c}"):
                if cand in tracked and cand not in ops_ref:
                    ops_ref[cand] = d

dev_seed: set[str] = set()
for t in set(targets) - PROD_TARGETS:
    files, _ = sh_edges(targets[t])
    dev_seed.update(files)
dev = closure(dev_seed)

BUILD = {
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitignore",
    ".pre-commit-config.yaml",
    "Makefile",
    "README.md",
    "pipeline/pyproject.toml",
    "pipeline/uv.lock",
}
# modules the tests import
test_import_cache: dict[str, set[str]] = {}


def test_imports(path: str) -> set[str]:
    if path not in test_import_cache:
        files, _ = py_edges(path)
        test_import_cache[path] = {f for f in files if f in modfile.values()}
    return test_import_cache[path]


def bucket(path: str) -> tuple[str, str]:
    if path in BUILD:
        return "build", "build/dev config"
    if path in prod and not path.startswith((".scratch/", "docs/", "resources/")):
        kind = "prod-code" if path.endswith((".py", ".sh")) else "prod-data"
        return kind, f"reached from {prod[path]}"
    if path.startswith((".scratch/", "finetune/", "fd-benchmark/", "resources/", "bench/")):
        return "research", path.split("/")[0]
    if path in {"kek.txt", "FULL-DUPLEX-RESEARCH.md", "PROJECT2-DESIGN-REVIEW.md"}:
        return "research", "root research note"
    if (
        path.startswith("docs/pi-results/")
        or path.startswith("docs/fdbench-results/")
        or path.startswith("docs/stress/")
    ):
        return "research", "recorded results"
    if path.startswith("docs/") or path.endswith(".md") and "/" not in path:
        return "docs", "documentation"
    if path.startswith("corpus/"):
        return (
            ("prod-data", f"read by {prod[path]}")
            if path in prod
            else ("corpus", "alternate demo corpus")
        )
    if path in prod:
        kind = "prod-code" if path.endswith((".py", ".sh")) else "prod-data"
        return kind, f"reached from {prod[path]}"
    if path.startswith(("tests/", "pipeline/tests/")):
        if path.endswith(".py"):
            imp = test_imports(path)
            if imp and all(i in prod for i in imp):
                return "test-prod", "imports only production modules"
            if imp and any(i in prod for i in imp):
                return "test-mixed", "imports production and dev modules"
            return (
                "test-dev",
                "imports only dev/unreferenced modules" if imp else "imports no repo module",
            )
        return "test-data", "fixture or helper"
    if path in dev:
        return "dev-tooling", f"reached only from dev target via {dev[path]}"
    if path in ops_ref:
        return "ops-referenced", f"named in {ops_ref[path]}"
    if path.startswith(("config/", "assets/")) and "eval" not in path:
        return (
            "ops-config",
            "config or asset in a production directory, not read by code (example, setup file)",
        )
    if path.startswith(
        (
            "src/",
            "pipeline/src/",
            "scripts/",
            "deploy/",
            "config/",
            "pipeline/config/",
            "assets/",
            "models/",
        )
    ):
        return "unreferenced", "in a production directory but nothing reaches it"
    return "other", ""


rows = []
for path, st in sorted(changed.items()):
    if st == "D":
        rows.append((path, st, "deleted", ""))
        continue
    b, ev = bucket(path)
    rows.append((path, st, b, ev))

# a fixture/helper is production if a production test names it
prod_tests = [r[0] for r in rows if r[2] == "test-prod"]
test_text = "\n".join((ROOT / t).read_text(errors="ignore") for t in prod_tests)
final = []
for path, st, b, ev in rows:
    parts = Path(path).parts
    fixture_dir = (
        parts[3]
        if len(parts) > 4 and parts[:2] == ("tests", "fixtures")
        else (parts[2] if len(parts) > 3 and parts[:2] == ("tests", "fixtures") else "")
    )
    if b == "test-data" and (
        Path(path).name in test_text or (fixture_dir and fixture_dir in test_text)
    ):
        b, ev = "test-prod-data", "named (file or fixture folder) by a production test"
    elif (
        b in {"dev-tooling", "unreferenced", "ops-referenced"}
        and not path.endswith((".py", ".sh"))
        and Path(path).name in test_text
    ):
        b, ev = "test-prod-data", "data file named by a production test"
    final.append((path, st, b, ev))

with (OUT / "manifest.tsv").open("w") as f:
    f.write("path\tstatus\tbucket\tevidence\n")
    for r in final:
        f.write("\t".join(r) + "\n")

count = Counter(r[2] for r in final)
print("files changed against origin/main:", len(final))
for k, v in count.most_common():
    print(f"  {v:5d}  {k}")
by = defaultdict(list)
for p, _st, b, _ev in final:
    by[b].append(p)
for k in ("unreferenced", "ops-referenced", "test-mixed", "test-dev", "other", "dev-tooling"):
    print(f"\n[{k}] {len(by[k])}")
    for p in by[k][:60]:
        print("  ", p)
sys.stdout.flush()


# ---- commit view -------------------------------------------------------------------------
INCLUDE = {"prod-code", "prod-data", "test-prod", "test-prod-data", "build", "ops-config"}
include_paths = {p for p, st, b, ev in final if b in INCLUDE}
(OUT / "include-core.txt").write_text("\n".join(sorted(include_paths)) + "\n")
fp = git("rev-list", "--reverse", "--first-parent", "origin/main..HEAD").split()
touch_core = mixed = only_other = 0
for c in fp:
    names = set(git("diff", "--name-only", f"{c}^1", c).split("\n")) - {""}
    core = names & include_paths
    if core and names - include_paths:
        mixed += 1
    if core:
        touch_core += 1
    else:
        only_other += 1
print(
    f"\nfirst-parent commits: {len(fp)} | touch core paths: {touch_core} "
    f"(of which also touch other paths: {mixed}) | touch only other paths: {only_other}"
)
print("core files:", len(include_paths))

rows2 = []
for p, _st, b, _ev in final:
    if b == "unreferenced" and p.endswith((".py", ".sh")):
        doc = ""
        try:
            if p.endswith(".py"):
                doc = (ast.get_docstring(ast.parse((ROOT / p).read_text())) or "").split("\n")[0]
            else:
                doc = next(
                    (
                        ln.lstrip("# ").strip()
                        for ln in (ROOT / p).read_text().splitlines()[1:6]
                        if ln.startswith("#")
                    ),
                    "",
                )
        except Exception:
            pass
        rows2.append((p, doc[:110]))
(OUT / "unreferenced_scripts.tsv").write_text("\n".join(f"{a}\t{b}" for a, b in rows2))
