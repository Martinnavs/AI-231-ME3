**Status:** delivered-for-review

### Line counts vs caps
- [`pipeline/src/voice_pipeline/dryrun.py`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/pipeline/src/voice_pipeline/dryrun.py): 240 / 264
- [`pipeline/tests/test_dryrun.py`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/pipeline/tests/test_dryrun.py): 25 / 31
- [`pipeline/config/eval/smart_home_prompts.jsonl`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/pipeline/config/eval/smart_home_prompts.jsonl): 47 / 47
- [`pipeline/config/eval/smart_home_system.txt`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/pipeline/config/eval/smart_home_system.txt): 6 / 6

### Proof
- 1 ✅ `cd pipeline && uv run --offline pytest -q` (`10 passed in 0.68s`)
- 2 ✅ `cd pipeline && uv run --offline ruff check . && uv run --offline ruff format --check . && uv run --offline mypy` (`Success: no issues found in 4 source files`)
- 3 ✅ `cd pipeline && uv run --offline python -m voice_pipeline.dryrun --help` (`  --run-id RUN_ID`)
- 4 ✅ `cd pipeline && wc -l config/eval/* && cmp config/eval/smart_home_prompts.jsonl /mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/.scratch/smart-home-tool/prompts.jsonl` (`  53 total`)
- 5 ✅ `wc -l` on the files in the table (`  318 total`)

### Open issues
None.

### Changed files
- [`pipeline/src/voice_pipeline/dryrun.py`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/pipeline/src/voice_pipeline/dryrun.py) (new)
- [`pipeline/tests/test_dryrun.py`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/pipeline/tests/test_dryrun.py) (new)
- [`pipeline/config/eval/smart_home_prompts.jsonl`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/pipeline/config/eval/smart_home_prompts.jsonl) (new)
- [`pipeline/config/eval/smart_home_system.txt`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/pipeline/config/eval/smart_home_system.txt) (new)
- [`.scratch/smart-home-tool/tickets/02-dryrun-and-scorer.md`](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/smart-home-tool/agy_work/work/.scratch/smart-home-tool/tickets/02-dryrun-and-scorer.md) (Status, Proof results, Execution Log updated)
