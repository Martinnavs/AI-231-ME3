"""Four harder tasks (H1-H4): multi-file site with working script, multi-file bug hunt, counting puzzle,
long log audit. Same contract as tasks.py: state-based scorer, known-good oracle, nothing run on the host."""

from __future__ import annotations

import hashlib
import itertools
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from tasks import TASKS, Task, answer_ok, read, serves_ok, sh, write

# ---------------------------------------------------------------- H1: site with a working script

H1_PROMPT = (
    "Make a small tip-calculator website in the folder site/ with three files: index.html (title "
    "'Tip Calculator'), style.css and tip.js. index.html must link both files, and contain a number input "
    "with id bill, a number input with id percent, a button with id go, and a span with id tip. tip.js "
    "must define a function computeTip(bill, percent) returning bill*percent/100 rounded to 2 decimals, "
    "and clicking the button must show that value in the span. The script must also work under node: end "
    "it with `if (typeof module !== 'undefined') module.exports = { computeTip };`. No server, no libraries."
)
H1_JS = (
    "function computeTip(bill, percent) {\n  return Math.round(bill * percent) / 100;\n}\n"
    "if (typeof document !== 'undefined') {\n"
    "  document.getElementById('go').addEventListener('click', function () {\n"
    "    var b = parseFloat(document.getElementById('bill').value);\n"
    "    var p = parseFloat(document.getElementById('percent').value);\n"
    "    document.getElementById('tip').textContent = computeTip(b, p).toFixed(2);\n  });\n}\n"
    "if (typeof module !== 'undefined') module.exports = { computeTip };\n"
)


def _h1_html() -> str:
    return (
        "<!doctype html>\n<html><head><title>Tip Calculator</title>\n<link rel=\"stylesheet\" href=\"style.css\">\n"
        "</head><body>\n<input id=\"bill\" type=\"number\"><input id=\"percent\" type=\"number\">\n"
        "<button id=\"go\">Go</button> <span id=\"tip\"></span>\n<script src=\"tip.js\"></script>\n</body></html>\n"
    )


async def _h1_score(root: Path, summary: str) -> dict[str, bool]:
    html, js = read(root, "site/index.html"), read(root, "site/tip.js")
    ids = {i: re.search(rf"<(input|button|span)[^>]*id\s*=\s*[\"']{i}[\"']", html, re.I) is not None
           for i in ("bill", "percent", "go", "tip")}
    probe = (
        "const {computeTip}=require('./site/tip.js');"
        "console.log(JSON.stringify([computeTip(80,15),computeTip(19.99,18),computeTip(0,10),computeTip(45.5,20)]))"
    )
    _, out = await sh(root, f"node -e \"{probe}\" 2>&1")
    return {
        "three_files": bool(html and js and read(root, "site/style.css")),
        "title": re.search(r"<title>\s*Tip Calculator\s*</title>", html, re.I) is not None,
        "linked": "style.css" in html and re.search(r"<script[^>]*src\s*=\s*[\"']tip\.js[\"']", html, re.I) is not None,
        "ids": all(ids.values()),
        "values": out.strip().splitlines()[-1:] == ["[12,3.6,0,9.1]"] if out.strip() else False,
        "wired": "click" in js and all(i in js for i in ("bill", "percent", "tip")),
        "serves": serves_ok(root, "/site/index.html", "Tip Calculator") if html else False,
    }


async def _h1_oracle(root: Path) -> str:
    write(root, "site/index.html", _h1_html())
    write(root, "site/style.css", "body { font-family: sans-serif; }\n")
    write(root, "site/tip.js", H1_JS)
    return "The tip calculator is ready.\nFiles: site/index.html, site/style.css, site/tip.js"


# ---------------------------------------------------------------- H2: multi-file bug hunt

H2_FILES = {
    "inventory/README.md": (
        "# Inventory\n\nInput lines are `sku,qty,price`. Blank lines and lines starting with `#` are ignored.\n"
        "`low_stock(items, n)` returns the SKUs with quantity strictly below n, sorted.\n"
        "`format_row` shows the price with two decimals.\n"
    ),
    "inventory/parse.py": (
        "def parse_line(line):\n    parts = line.split(',')\n"
        "    return {'sku': parts[0], 'qty': int(parts[1]), 'price': float(parts[2])}\n\n\n"
        "def parse_text(text):\n    return [parse_line(ln) for ln in text.splitlines()]\n"
    ),
    "inventory/stock.py": (
        "def total_value(items):\n    return round(sum(i['qty'] * i['price'] for i in items), 2)\n"
    ),
    "inventory/report.py": (
        "def low_stock(items, n):\n    return sorted(i['sku'] for i in items if i['qty'] <= n)\n\n\n"
        "def format_row(item):\n    return f\"{item['sku']:<8}{item['qty']:>5}{item['price']:>9.1f}\"\n"
    ),
    "inventory/test_inventory.py": (
        "import unittest\n\nfrom parse import parse_text\nfrom report import low_stock\nfrom stock import total_value\n\n\n"
        "TEXT = '# stock\\nA1,5,2.5\\n\\nB2,0,10\\nC3,12,1.25\\n'\n\n\nclass InventoryTests(unittest.TestCase):\n"
        "    def test_parse_skips_noise(self):\n        self.assertEqual([i['sku'] for i in parse_text(TEXT)], ['A1', 'B2', 'C3'])\n\n"
        "    def test_total(self):\n        self.assertEqual(total_value(parse_text(TEXT)), 27.5)\n\n"
        "    def test_low_stock(self):\n        self.assertEqual(low_stock(parse_text(TEXT), 5), ['B2'])\n\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    ),
}
H2_PROMPT = (
    "In the folder inventory/ the tests fail when run with `python3 -m unittest` from that folder. "
    "Make the code pass the tests and behave as described in README.md. Do not change the test file."
)
H2_FIXES = {
    "inventory/parse.py": (
        "    return [parse_line(ln) for ln in text.splitlines()]",
        "    return [parse_line(ln) for ln in text.splitlines() if ln.strip() and not ln.startswith('#')]",
    ),
    "inventory/report.py": ("i['qty'] <= n", "i['qty'] < n"),
}
H2_HIDDEN = (
    "import unittest\n\nfrom report import format_row\n\n\nclass Hidden(unittest.TestCase):\n"
    "    def test_row(self):\n        self.assertEqual(format_row({'sku': 'A1', 'qty': 5, 'price': 2.5}), 'A1          5     2.50')\n"
    "    def test_row_round(self):\n        self.assertEqual(format_row({'sku': 'C3', 'qty': 12, 'price': 1.256}), 'C3         12     1.26')\n"
)


def _h2_setup(root: Path) -> None:
    for rel, text in H2_FILES.items():
        write(root, rel, text)


async def _h2_score(root: Path, summary: str) -> dict[str, bool]:
    rc, _ = await sh(root, "cd inventory && python3 -m unittest 2>&1 >/dev/null")
    write(root, "_hidden/test_hidden.py", H2_HIDDEN)
    rc_hidden, _ = await sh(root, "cd inventory && PYTHONPATH=. python3 -m unittest discover -s ../_hidden -p 'test_hidden.py' 2>&1 >/dev/null")
    same = hashlib.sha256(read(root, "inventory/test_inventory.py").encode()).hexdigest() == \
        hashlib.sha256(H2_FILES["inventory/test_inventory.py"].encode()).hexdigest()
    return {"visible_pass": rc == 0, "readme_behaviour": rc_hidden == 0, "test_file_unchanged": same}


async def _h2_oracle(root: Path) -> str:
    for rel, (old, new) in H2_FIXES.items():
        write(root, rel, read(root, rel).replace(old, new))
    write(root, "inventory/report.py", read(root, "inventory/report.py").replace(">9.1f", ">9.2f"))
    return "Fixed the parser, the low-stock rule and the price format.\nFiles: inventory/parse.py, inventory/report.py"


# ---------------------------------------------------------------- H3: counting puzzle

def _h3_truth() -> int:
    coins = [(1, 4), (5, 4), (10, 4), (25, 4), (50, 4), (100, 2)]
    return sum(1 for combo in itertools.product(*(range(m + 1) for _, m in coins))
               if sum(c * v for (v, _), c in zip(coins, combo, strict=True)) == 200)


H3_PROMPT = (
    "In how many ways can you make exactly 200 cents using at most 4 coins of each of the values 1, 5, 10, "
    "25 and 50 cents, and at most 2 coins of 100 cents? Coins of the same value are indistinguishable, "
    "and using none of a value is allowed. You may use the bash tool; python3 is available. "
    "End your reply with 'Answer: <number>'."
)


async def _h3_score(root: Path, summary: str) -> dict[str, bool]:
    return {"answer": answer_ok(summary, float(_h3_truth()), 0.0)}


async def _h3_oracle(root: Path) -> str:
    return f"Counted {_h3_truth()} ways. Answer: {_h3_truth()}"


# ---------------------------------------------------------------- H4: long log audit

def _h4_lines() -> list[str]:
    rnd = random.Random(11)
    ips = ["10.0.0.%d" % n for n in (4, 9, 17, 23, 31, 42)]
    weights = [9, 7, 5, 3, 2, 1]
    paths = {"/home": 40, "/search": 120, "/cart": 90, "/api/items": 60, "/login": 30}
    statuses = [200] * 14 + [404] * 3 + [500, 502, 503]
    out = []
    for n in range(480):
        hour = rnd.choices(range(8, 20), weights=[1, 2, 3, 5, 9, 4, 3, 2, 3, 2, 1, 1])[0]
        path = rnd.choice(list(paths))
        ms = max(5, int(rnd.gauss(paths[path], 12)))
        out.append(f"{rnd.choices(ips, weights)[0]} [2026-10-{1 + n % 5:02d} {hour:02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}] "
                   f"GET {path} {rnd.choice(statuses)} {ms}")
    return out


def _h4_truth() -> dict[str, object]:
    lines = [ln.split() for ln in _h4_lines()]
    ips = Counter(p[0] for p in lines)
    hours = Counter(p[2][:2] for p in lines)
    times: dict[str, list[int]] = defaultdict(list)
    for p in lines:
        times[p[4]].append(int(p[6]))
    means = {path: sum(v) / len(v) for path, v in times.items()}
    return {
        "top": [ip for ip, _ in ips.most_common(3)],
        "tied": ips.most_common(4)[2][1] == ips.most_common(4)[3][1],
        "errors": sum(1 for p in lines if p[5].startswith("5")),
        "hour": hours.most_common(1)[0][0],
        "slowest": max(means, key=means.get),
    }


H4_PROMPT = (
    "The folder logs/ holds access logs (lines like `10.0.0.4 [2026-10-02 13:05:41] GET /cart 200 87`, the "
    "last number being milliseconds). Write report.md with exactly these four lines: "
    "`Top IPs: a, b, c` (the three IPs with the most requests, most first), `Server errors: N` "
    "(status 500-599), `Busiest hour: HH` (two digits, the hour of day with the most requests, all days "
    "together), `Slowest path: /x` (the path with the highest mean milliseconds)."
)


def _h4_setup(root: Path) -> None:
    lines = _h4_lines()
    for i in range(4):
        write(root, f"logs/access-{i + 1}.log", "\n".join(lines[i::4]) + "\n")


async def _h4_score(root: Path, summary: str) -> dict[str, bool]:
    text, truth = read(root, "report.md"), _h4_truth()
    top = re.search(r"Top IPs:\s*(.+)", text)
    errors = re.search(r"Server errors:\s*(\d+)", text)
    hour = re.search(r"Busiest hour:\s*(\d{2})", text)
    slow = re.search(r"Slowest path:\s*(\S+)", text)
    return {
        "top_ips": top is not None and [s.strip() for s in top.group(1).split(",")] == truth["top"],
        "errors": errors is not None and int(errors.group(1)) == truth["errors"],
        "hour": hour is not None and hour.group(1) == truth["hour"],
        "slowest": slow is not None and slow.group(1) == truth["slowest"],
    }


async def _h4_oracle(root: Path) -> str:
    t = _h4_truth()
    write(root, "report.md", f"Top IPs: {', '.join(t['top'])}\nServer errors: {t['errors']}\n"
                             f"Busiest hour: {t['hour']}\nSlowest path: {t['slowest']}\n")
    return "Report written.\nFiles: report.md"


HARD = [
    Task("H1", "web", H1_PROMPT, lambda root: None, _h1_score, _h1_oracle),
    Task("H2", "terminal", H2_PROMPT, _h2_setup, _h2_score, _h2_oracle),
    Task("H3", "math", H3_PROMPT, lambda root: None, _h3_score, _h3_oracle),
    Task("H4", "terminal", H4_PROMPT, _h4_setup, _h4_score, _h4_oracle),
]


TASKS.update({t.id: t for t in HARD})
