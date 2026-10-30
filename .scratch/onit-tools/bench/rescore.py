"""Re-score saved runs with the current scorers (only for tasks whose scorer was fixed after seeing data).
Writes <run>.rescored.jsonl next to the original; the original file is never changed."""
import asyncio, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "probe")]
import run_bench  # noqa: F401  (registers every task)
from tasks import TASKS

run, only = sys.argv[1], set(sys.argv[2].split(","))
src = HERE / "results" / f"{run}.jsonl"
rows = [json.loads(line) for line in src.open()]
out = []
for row in rows:
    if row["task"] in only:
        sandbox = run_bench.RUNS / run / row["arm"] / f"{row['task']}-{row['rep']}"
        checks = asyncio.run(TASKS[row["task"]].score(sandbox, row["summary"]))
        new = {**row, "checks": checks, "pass": all(checks.values())}
        if new["pass"] != row["pass"]:
            new["rescored"] = f"was {'pass' if row['pass'] else 'fail'}"
            print(f"{row['task']}-{row['rep']}: {new['rescored']} -> {'pass' if new['pass'] else 'fail'}  {[k for k, v in checks.items() if not v]}")
        row = new
    out.append(row)
(HERE / "results" / f"{run}.rescored.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
