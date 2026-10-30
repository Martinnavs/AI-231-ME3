"""Thirteen realistic spoken requests (kind "limit") that push the delegate loop: questions over a document folder,
a todo app with tests and git, a security check, messy exports, a 7 MB log, a git summary, four math problems that
need code (no numpy in the sandbox), a 75 s build, and a script that fails until the model fixes how it runs it.
Same contract as tasks.py: state-based scorer, known-good oracle, model-written code runs only in the sandbox."""

from __future__ import annotations

import csv
import io
import json
import random
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from tasks import (T1_COMMANDS, TASKS, Task, _finding_ok, _sections, answer_ok, read, serves_ok, sh, write)

FOOT = " End your reply with 'Answer: <number>'."
NOT_FOUND = re.compile(
    r"no (mention|information|policy|record|details|section)|not (find|found|mention|cover|say|include|address)"
    r"|couldn't find|could not find|can't find|cannot find|doesn't (say|mention|cover|include|have|appear)"
    r"|does not (say|mention|cover|include|have|appear)|isn't (covered|mentioned|in)|nothing (about|on|in)",
    re.IGNORECASE,
)


def _words(rnd: random.Random, n: int) -> str:
    vocab = ("office team budget review plan travel desk laptop lunch parking badge visitor printer cleaning meeting room "
             "booking invoice supplier safety drill training survey").split()
    return " ".join(rnd.choice(vocab) for _ in range(n))


# ---------------------------------------------------------------- D1 / D2: questions over a document folder

def _company() -> dict[str, str]:
    rnd = random.Random(3)
    docs = {
        "company/hr-leave.md": "# Leave policy\n\nEmployees with at least 3 years of service get 20 vacation days per year. "
                               "Newer employees get 15.\n",
        "company/hr-carryover.md": "# Carry-over\n\nAt most 5 unused vacation days carry over to the next year. "
                                   "Carried days expire on 31 March.\n",
        "company/archive/hr-leave-2019.md": "# Leave policy (superseded in 2020)\n\nAfter 3 years: 18 vacation days. "
                                            "Carry-over: 10 days.\n",
        "company/facilities-pets.md": "# Pets\n\nPets are not allowed in the office, except guide dogs.\n",
    }
    for name in ("expenses", "travel", "security", "onboarding", "parking", "cafeteria", "it-support", "visitors"):
        docs[f"company/{name}.md"] = f"# {name.title()}\n\n" + _words(rnd, 90) + "\n"
    log = [f"2026-03-{1 + i % 28:02d} user{rnd.randint(10, 99)} viewed {rnd.choice(['vacation calendar', 'payslip', 'rota', 'handbook'])} "
           f"{_words(rnd, 4)}" for i in range(7000)]
    docs["company/logs/audit-trail.log"] = "\n".join(log) + "\n"
    return docs


D1_PROMPT = ("Hey, how many vacation days do I get after three years at the company, and how many can I carry over? "
             "The policy documents are in the folder company/. Tell me which files you got it from.")
D2_PROMPT = "Quick one: what's the company's policy on pet insurance? The documents are in the folder company/."


def _company_setup(root: Path) -> None:
    for rel, text in _company().items():
        write(root, rel, text)


async def _d1_score(root: Path, summary: str) -> dict[str, bool]:
    return {
        "twenty_days": re.search(r"\b20\b", summary) is not None,
        "carry_five": re.search(r"\b(5|five)\b", summary, re.I) is not None,
        "cites_leave": "hr-leave.md" in summary,
        "cites_carryover": "hr-carryover.md" in summary,
    }


async def _d1_oracle(root: Path) -> str:
    return "After 3 years you get 20 vacation days and can carry over 5. Sources: hr-leave.md and hr-carryover.md."


async def _d2_score(root: Path, summary: str) -> dict[str, bool]:
    return {
        "says_not_found": NOT_FOUND.search(summary) is not None,
        "nothing_invented": re.search(r"\$\s?\d|\d+\s?%|covers? (up to|all)", summary, re.I) is None,
    }


async def _d2_oracle(root: Path) -> str:
    return "I couldn't find anything about pet insurance in those documents; the only pet rule is that pets aren't allowed in the office."


# ---------------------------------------------------------------- B1: todo app with tests and git

B1_PROMPT = (
    "Make me a small todo-list web app in the folder app/ that I can open in the browser, with tests, and commit it "
    "to git there with the message 'todo app' (use -c user.name=Test -c user.email=test@example.com). I want an "
    "index.html titled 'Todo' that loads todo.js. todo.js has addTodo(list, text), toggleTodo(list, id), "
    "removeTodo(list, id) and remaining(list), none of which change the list they are given: a todo is "
    "{id, text, done}, a new id is the highest id plus one (1 for an empty list), and remaining counts the "
    "todos not done. Export them with `if (typeof module !== 'undefined') module.exports = {...}` so node can "
    "load them, and put tests in todo.test.js that pass with `node --test`."
)
B1_JS = (
    "function addTodo(list, text) {\n  const id = list.reduce((m, t) => Math.max(m, t.id), 0) + 1;\n"
    "  return [...list, { id, text, done: false }];\n}\n"
    "function toggleTodo(list, id) {\n  return list.map((t) => (t.id === id ? { ...t, done: !t.done } : t));\n}\n"
    "function removeTodo(list, id) {\n  return list.filter((t) => t.id !== id);\n}\n"
    "function remaining(list) {\n  return list.filter((t) => !t.done).length;\n}\n"
    "if (typeof module !== 'undefined') module.exports = { addTodo, toggleTodo, removeTodo, remaining };\n"
)
B1_TEST = (
    "const test = require('node:test');\nconst assert = require('node:assert');\nconst t = require('./todo.js');\n\n"
    "test('add', () => assert.strictEqual(t.addTodo([], 'x')[0].id, 1));\n"
    "test('toggle', () => assert.strictEqual(t.toggleTodo(t.addTodo([], 'x'), 1)[0].done, true));\n"
    "test('remove', () => assert.strictEqual(t.removeTodo(t.addTodo([], 'x'), 1).length, 0));\n"
    "test('remaining', () => assert.strictEqual(t.remaining(t.addTodo(t.addTodo([], 'a'), 'b')), 2));\n"
)


async def _b1_score(root: Path, summary: str) -> dict[str, bool]:
    html = read(root, "app/index.html")
    rc, out = await sh(root, "cd app && node --test 2>&1")
    count = re.search(r"tests\s+(\d+)", out)
    probe = ("const t=require('./app/todo.js');const l=[];const a=t.addTodo(l,'x');const b=t.addTodo(a,'y');"
             "const c=t.toggleTodo(b,1);const d=t.removeTodo(c,2);"
             "console.log(JSON.stringify([l.length,a.length,b.map(x=>x.id),c[0].done,t.remaining(c),d.length,t.remaining(d),"
             "t.remaining(b),b[0].done]))")
    _, got = await sh(root, f"node -e \"{probe}\" 2>&1")
    _, subject = await sh(root, "git -C app log -1 --format=%s 2>/dev/null")
    _, status = await sh(root, "git -C app status --porcelain --untracked-files=no 2>/dev/null")
    _, tracked = await sh(root, "git -C app ls-files 2>/dev/null")
    return {
        "html": re.search(r"<title>[^<]*todo[^<]*</title>", html, re.I) is not None
                and re.search(r"<script[^>]*src\s*=\s*[\"']todo\.js", html, re.I) is not None,
        "own_tests_pass": rc == 0 and count is not None and int(count.group(1)) >= 4,
        "behaviour": got.strip().splitlines()[-1:] == ['[0,1,[1,2],true,1,1,0,2,false]'] if got.strip() else False,
        "committed": subject.strip() == "todo app" and status.strip() == "",
        "files_tracked": all(f in tracked.split() for f in ("index.html", "todo.js", "todo.test.js")),
        "serves": serves_ok(root, "/app/index.html", "todo.js") if html else False,
    }


async def _b1_oracle(root: Path) -> str:
    write(root, "app/index.html", '<!doctype html><html><head><title>Todo</title></head><body><ul id="l"></ul>'
                                  '<script src="todo.js"></script></body></html>\n')
    write(root, "app/todo.js", B1_JS)
    write(root, "app/todo.test.js", B1_TEST)
    await sh(root, "cd app && git init -q && git add -A && git -c user.name=Test -c user.email=test@example.com commit -qm 'todo app'")
    return "The todo app is built, tested and committed.\nFiles: app/index.html, app/todo.js, app/todo.test.js"


# ---------------------------------------------------------------- A1: vague security check

A1_PROMPT = (
    "Can you do a quick security check of this laptop, nothing that changes anything? Write it up in security.md "
    "with these sections: 'Login accounts' (the account names with a real login shell), 'Setuid count' (how many setuid "
    "files sit directly in /usr/bin), 'World-writable in /etc' (list them, or none), 'SSH host keys' (each private host "
    "key file in /etc/ssh with its permission mode, like ssh_host_ed25519_key 600), and 'Not checked' (anything you "
    "could not look at and why)."
)
A1_SECTIONS = ("Login accounts", "Setuid count", "World-writable in /etc", "SSH host keys", "Not checked")
A1_KEYS = "find /etc/ssh -maxdepth 1 -name 'ssh_host_*_key' -printf '%f %m\\n' 2>/dev/null"
A1_SETUID = "find /usr/bin -maxdepth 1 -perm -4000 -type f 2>/dev/null | wc -l"


def _says_none(section: str) -> bool:
    """True if the section opens with 'none' and lists no path (an explanation after it is fine)."""
    body = [ln.strip(" -*`\t") for ln in section.splitlines() if ln.strip()]
    return bool(body) and body[0].lower().startswith("none") and not any(ln.startswith("/") for ln in body)


async def _a1_score(root: Path, summary: str) -> dict[str, bool]:
    sections = _sections(read(root, "security.md"))
    present = {s: s in sections for s in A1_SECTIONS}
    checks = {"file_and_sections": all(present.values())}
    if not all(present.values()):
        return checks
    _, accounts = await sh(root, T1_COMMANDS[2])
    _, writable = await sh(root, T1_COMMANDS[0])
    _, count = await sh(root, A1_SETUID)
    _, keys = await sh(root, A1_KEYS)
    key_lines = [ln.split() for ln in keys.splitlines() if ln.strip()]
    ssh_body = sections["SSH host keys"]
    checks["accounts"] = _finding_ok(sections["Login accounts"], accounts.split())
    checks["setuid_count"] = re.search(rf"\b{int(count.strip() or 0)}\b", sections["Setuid count"]) is not None
    checks["world_writable"] = (_finding_ok(sections["World-writable in /etc"], writable.split()) if writable.split()
                                else _says_none(sections["World-writable in /etc"]))
    checks["ssh_keys"] = bool(key_lines) and all(
        any(name in ln and re.search(rf"\b{mode}\b", ln) for ln in ssh_body.splitlines()) for name, mode in key_lines)
    checks["not_checked"] = bool(sections["Not checked"].strip())
    return checks


async def _a1_oracle(root: Path) -> str:
    _, accounts = await sh(root, T1_COMMANDS[2])
    _, writable = await sh(root, T1_COMMANDS[0])
    _, count = await sh(root, A1_SETUID)
    _, keys = await sh(root, A1_KEYS)
    text = (f"## Login accounts\n{accounts}\n## Setuid count\n{count.strip()}\n## World-writable in /etc\n"
            f"{writable or 'none'}\n## SSH host keys\n{keys}\n## Not checked\nShadow file and firewall rules need root.\n")
    write(root, "security.md", text)
    return "Security check written.\nFiles: security.md"


# ---------------------------------------------------------------- P1: messy exports

def _p1_files() -> dict[str, str]:
    rnd = random.Random(8)
    names = {"c01": "Marta Ruiz", "c02": "Ken Obi", "c03": "Lena Fischer", "c04": "Tom Hale", "c05": "Ayo Bello"}
    files: dict[str, str] = {}
    july = ["customer,amount_usd,date"] + [f"{rnd.choice(list(names.values()))},{rnd.randint(500, 9000) / 100:.2f},2026-07-{1 + i % 28:02d}" for i in range(14)]
    aug = [json.dumps({"customer": rnd.choice(list(names.values())), "amount": rnd.randint(500, 9000) / 100, "date": f"2026-08-{1 + i % 28:02d}"}) for i in range(14)]
    sep = ["customer_id,amount_cents,date"] + [f"{rnd.choice(list(names))},{rnd.randint(500, 9000)},2026-09-{1 + i % 28:02d}" for i in range(14)]
    june = ["customer,amount_usd,date"] + [f"{names['c04']},{rnd.randint(40000, 90000) / 100:.2f},2026-06-{1 + i % 28:02d}" for i in range(3)]
    refunds = ["customer,refund_usd,date", f"{names['c02']},{rnd.randint(2000, 4000) / 100:.2f},2026-08-20",
               f"{names['c05']},{rnd.randint(1000, 3000) / 100:.2f},2026-09-05"]
    files["exports/orders_2026_07.csv"] = "\n".join(july) + "\n"
    files["exports/orders_2026_08.jsonl"] = "\n".join(aug) + "\n"
    files["exports/orders_sep.csv"] = "\n".join(sep) + "\n"
    files["exports/orders_2026_06.csv"] = "\n".join(june) + "\n"
    files["exports/refunds.csv"] = "\n".join(refunds) + "\n"
    files["exports/customers.csv"] = "id,name\n" + "\n".join(f"{k},{v}" for k, v in names.items()) + "\n"
    files["exports/README.txt"] = ("Order exports. One file per month, formats differ. Amounts are dollars except the "
                                   "September file (cents, customers by id; see customers.csv). refunds.csv is in dollars.\n")
    return files


def _p1_truth(files: dict[str, str]) -> tuple[str, float, bool]:
    names = {r["id"]: r["name"] for r in csv.DictReader(io.StringIO(files["exports/customers.csv"]))}
    total: dict[str, float] = {}
    for r in csv.DictReader(io.StringIO(files["exports/orders_2026_07.csv"])):
        total[r["customer"]] = total.get(r["customer"], 0) + float(r["amount_usd"])
    for line in files["exports/orders_2026_08.jsonl"].splitlines():
        row = json.loads(line)
        total[row["customer"]] = total.get(row["customer"], 0) + row["amount"]
    for r in csv.DictReader(io.StringIO(files["exports/orders_sep.csv"])):
        total[names[r["customer_id"]]] = total.get(names[r["customer_id"]], 0) + int(r["amount_cents"]) / 100
    for r in csv.DictReader(io.StringIO(files["exports/refunds.csv"])):
        total[r["customer"]] -= float(r["refund_usd"])
    ranked = sorted(total.items(), key=lambda kv: -kv[1])
    return ranked[0][0], round(ranked[0][1], 2), abs(ranked[0][1] - ranked[1][1]) < 0.02


P1_PROMPT = ("Which customer spent the most in the third quarter, July to September, after refunds? Tell me their name "
             "and the total. The exports are in the folder exports/." + FOOT)


def _p1_setup(root: Path) -> None:
    for rel, text in _p1_files().items():
        write(root, rel, text)


async def _p1_score(root: Path, summary: str) -> dict[str, bool]:
    name, amount, _ = _p1_truth(_p1_files())
    return {"name": name.lower() in summary.lower(), "total": answer_ok(summary, amount, 0.011)}


async def _p1_oracle(root: Path) -> str:
    name, amount, _ = _p1_truth(_p1_files())
    return f"{name} spent the most, {amount} dollars. Answer: {amount}"


# ---------------------------------------------------------------- P2: a 7 MB log

def _p2_log() -> str:
    rnd = random.Random(21)
    lines = []
    for i in range(120_000):
        level = rnd.choices(["INFO", "WARN", "ERROR"], [80, 12, 8])[0]
        pool = ["E101", "E102", "E103", "E104", "E105"] if level == "ERROR" else ["E101", "E101", "E101", "E104", "E200"]
        weights = [5, 9, 14, 7, 3] if level == "ERROR" else [1, 1, 1, 1, 1]
        code = rnd.choices(pool, weights)[0]
        lines.append(f"2026-10-{1 + i % 28:02d}T{i % 24:02d}:{i % 60:02d}:{i % 59:02d}Z {level} code={code} "
                     f"request {i} handled by worker{i % 7} in {rnd.randint(2, 900)}ms")
    return "\n".join(lines) + "\n"


@lru_cache(maxsize=1)
def _p2_truth() -> tuple[int, str]:
    codes: dict[str, int] = {}
    count = 0
    for line in _p2_log().splitlines():
        if " ERROR " in line:
            count += 1
            code = line.split("code=")[1].split()[0]
            codes[code] = codes.get(code, 0) + 1
    return count, max(codes, key=codes.get)


P2_PROMPT = ("How many ERROR lines are in logs/server.log, and which error code shows up most often among just those "
             "ERROR lines? Say the count and the code.")


def _p2_setup(root: Path) -> None:
    write(root, "logs/server.log", _p2_log())


async def _p2_score(root: Path, summary: str) -> dict[str, bool]:
    count, code = _p2_truth()
    return {"count": re.search(rf"\b{count:,}\b|\b{count}\b", summary) is not None, "code": code in summary}


async def _p2_oracle(root: Path) -> str:
    count, code = _p2_truth()
    return f"There are {count} ERROR lines and the most common code is {code}."


# ---------------------------------------------------------------- R1: git summary

R1_COMMITS = [
    ("Bo", "start engine", ["engine.py"]), ("Bo", "start ui", ["ui.py"]), ("Cy", "add readme", ["README.md"]),
    ("TAG", "v1", []),
    ("Ana", "engine: parse input", ["engine.py"]), ("Ana", "engine: handle errors", ["engine.py", "ui.py"]),
    ("Bo", "ui: layout", ["ui.py"]), ("Cy", "engine: speed up", ["engine.py"]),
    ("Ana", "docs", ["README.md"]), ("Bo", "engine: cache", ["engine.py"]), ("Cy", "config", ["config.toml"]),
]
R1_PROMPT = ("In the repo in the folder history/, since the v1 tag: how many commits were made, which file was changed "
             "by the most commits, and who wrote the most commits? Tell me all three.")


def _r1_setup(root: Path) -> None:
    repo = root / "history"
    repo.mkdir(parents=True)
    env_base = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PATH": "/usr/bin:/bin"}
    run = lambda *a, env=None: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,  # noqa: E731
                                              env={**env_base, **(env or {})})
    run("init", "-q")
    for n, (author, message, files) in enumerate(R1_COMMITS):
        if author == "TAG":
            run("tag", message)
            continue
        for f in files:
            with (repo / f).open("a") as fh:
                fh.write(f"{message} {n}\n")
        run("add", "-A")
        env = {"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
               "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com"}
        run("-c", "commit.gpgsign=false", "commit", "-q", "-m", message, env=env)


async def _r1_score(root: Path, summary: str) -> dict[str, bool]:
    after = R1_COMMITS[4:]
    return {
        "count": re.search(rf"\b({len(after)}|seven)\b", summary, re.I) is not None,
        "file": "engine.py" in summary,
        "author": "Ana" in summary,
        "repo_untouched": (await sh(root, "git -C history log --oneline 2>/dev/null | wc -l"))[1].strip() == "10",
    }


async def _r1_oracle(root: Path) -> str:
    return "Seven commits since v1; engine.py changed most, and Ana wrote the most."


# ---------------------------------------------------------------- K1-K4: math that needs code

@lru_cache(maxsize=1)
def _k1() -> int:
    return sum(1 for n in range(1, 10**6 + 1) if n % 3 and n % 5 and n % 7 and sum(map(int, str(n))) == 20)


@lru_cache(maxsize=1)
def _k2() -> int:
    limit = 3_000_000
    sieve = bytearray([1]) * limit
    sieve[0:2] = b"\x00\x00"
    for i in range(2, int(limit**0.5) + 1):
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(sieve[i * i::i]))
    return sum(i for i in range(3, limit, 10) if sieve[i])


@lru_cache(maxsize=1)
def _k3() -> float:
    n = 5
    cells = [(r, c) for r in range(n) for c in range(n)]
    index = {cell: i for i, cell in enumerate(cells)}
    target = index[(n - 1, n - 1)]
    size = len(cells)
    a = [[0.0] * (size + 1) for _ in range(size)]
    for (r, c), i in index.items():
        if i == target:
            a[i][i] = 1.0
            continue
        nbrs = [(r + dr, c + dc) for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)) if 0 <= r + dr < n and 0 <= c + dc < n]
        a[i][i] = 1.0
        for nb in nbrs:
            a[i][index[nb]] -= 1.0 / len(nbrs)
        a[i][size] = 1.0
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(a[row][col]))
        a[col], a[pivot] = a[pivot], a[col]
        for row in range(size):
            if row != col and a[row][col]:
                f = a[row][col] / a[col][col]
                a[row] = [x - f * y for x, y in zip(a[row], a[col], strict=True)]
    return a[0][size] / a[0][0]


@lru_cache(maxsize=1)
def _k4() -> float:
    import math
    g, k, v0, angle, dt = 9.81, 0.01, 40.0, math.radians(35), 1e-4
    x, y, vx, vy = 0.0, 0.0, v0 * math.cos(angle), v0 * math.sin(angle)

    def acc(vx: float, vy: float) -> tuple[float, float]:
        speed = math.hypot(vx, vy)
        return -k * speed * vx, -g - k * speed * vy

    while True:
        k1 = acc(vx, vy)
        k2 = acc(vx + dt / 2 * k1[0], vy + dt / 2 * k1[1])
        k3 = acc(vx + dt / 2 * k2[0], vy + dt / 2 * k2[1])
        k4 = acc(vx + dt * k3[0], vy + dt * k3[1])
        nvx = vx + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        nvy = vy + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        nx, ny = x + vx * dt, y + vy * dt
        if ny < 0:
            return x + (nx - x) * y / (y - ny)
        x, y, vx, vy = nx, ny, nvx, nvy


K_TASKS = {
    "K1": ("How many whole numbers from 1 to 1,000,000 are not divisible by 3, 5 or 7 and have digits that add up to "
           "exactly 20?" + FOOT, lambda: float(_k1()), 0.0),
    "K2": ("What is the sum of all prime numbers below 3,000,000 whose last digit is 3?" + FOOT,
           lambda: float(_k2()), 0.0),
    "K3": ("A token sits on the corner of a 5 by 5 grid and moves each step to a uniformly random neighbouring cell "
           "(up, down, left or right, staying on the grid). On average, how many steps does it take to first reach "
           "the opposite corner? Give three decimals." + FOOT, _k3, 0.005),
    "K4": ("A ball is launched from the ground at 40 metres per second, 35 degrees above horizontal. Gravity is 9.81, "
           "and air drag decelerates it by 0.01 times its speed times its velocity (per metre). How far from the "
           "launch point does it land? Two decimals, metres." + FOOT, _k4, 0.05),
}


def _k_task(tid: str) -> Task:
    prompt, truth, tol = K_TASKS[tid]

    async def score(root: Path, summary: str) -> dict[str, bool]:
        return {"answer": answer_ok(summary, truth(), tol)}

    async def oracle(root: Path) -> str:
        return f"Computed it. Answer: {truth()}"

    return Task(tid, "limit", prompt + " The sandbox has python3 but no numpy.", lambda r: None, score, oracle)


# ---------------------------------------------------------------- O1 / O2: slow build, script that needs setup

O1_SCRIPT = ("#!/bin/bash\nfor i in 1 2 3 4 5; do echo \"step $i of 5\"; sleep 15; done\n"
             "echo 'BUILD OK' > \"$(dirname \"$0\")/build.log\"\necho 'build finished'\n")
O1_PROMPT = "Can you run project/build.sh and tell me whether the build worked? It takes a little over a minute."


def _o1_setup(root: Path) -> None:
    write(root, "project/build.sh", O1_SCRIPT)
    (root / "project/build.sh").chmod(0o755)


async def _o1_score(root: Path, summary: str) -> dict[str, bool]:
    return {
        "ran_to_the_end": read(root, "project/build.log").strip() == "BUILD OK",
        "reported_success": re.search(r"succe|worked|passed|\bok\b|complete|finished|done", summary, re.I) is not None
                            and not re.search(r"(build|it) (failed|did not (finish|complete|succeed)|didn't (finish|complete))"
                                              r"|not (finished|complete)|still running|in the background|started the build", summary, re.I),
    }


async def _o1_oracle(root: Path) -> str:
    write(root, "project/build.log", "BUILD OK\n")
    return "The build worked and finished OK."


O2_SCRIPT = ('#!/bin/bash\nhere="$(cd "$(dirname "$0")" && pwd)"\n'
             '[ -n "$TARGET" ] || { echo "error: TARGET is not set" >&2; exit 2; }\n'
             '[ -d "$TARGET" ] || { echo "error: $TARGET is not a directory" >&2; exit 3; }\n'
             'cp -r "$here"/dist/. "$TARGET"/ && echo "deployed to $TARGET"\n')
O2_PROMPT = ("Please deploy the release by running release/deploy.sh, and put it in a new folder called staging/ in the "
             "main folder. Don't edit the script.")


def _o2_setup(root: Path) -> None:
    write(root, "release/deploy.sh", O2_SCRIPT)
    (root / "release/deploy.sh").chmod(0o755)
    write(root, "release/dist/index.html", "<html><body>v2</body></html>\n")
    write(root, "release/dist/app.js", "console.log('v2');\n")


async def _o2_score(root: Path, summary: str) -> dict[str, bool]:
    return {
        "index_deployed": read(root, "staging/index.html") == "<html><body>v2</body></html>\n",
        "app_deployed": read(root, "staging/app.js") == "console.log('v2');\n",
        "script_unchanged": read(root, "release/deploy.sh") == O2_SCRIPT,
    }


async def _o2_oracle(root: Path) -> str:
    await sh(root, "mkdir -p staging && TARGET=staging release/deploy.sh")
    return "Deployed the release into staging."


LIMIT = [
    Task("D1", "limit", D1_PROMPT, _company_setup, _d1_score, _d1_oracle),
    Task("D2", "limit", D2_PROMPT, _company_setup, _d2_score, _d2_oracle),
    Task("B1", "limit", B1_PROMPT, lambda r: None, _b1_score, _b1_oracle),
    Task("A1", "limit", A1_PROMPT, lambda r: None, _a1_score, _a1_oracle),
    Task("P1", "limit", P1_PROMPT, _p1_setup, _p1_score, _p1_oracle),
    Task("P2", "limit", P2_PROMPT, _p2_setup, _p2_score, _p2_oracle),
    Task("R1", "limit", R1_PROMPT, _r1_setup, _r1_score, _r1_oracle),
    *(_k_task(t) for t in K_TASKS),
    Task("O1", "limit", O1_PROMPT, _o1_setup, _o1_score, _o1_oracle),
    Task("O2", "limit", O2_PROMPT, _o2_setup, _o2_score, _o2_oracle),
]
TASKS.update({t.id: t for t in LIMIT})
