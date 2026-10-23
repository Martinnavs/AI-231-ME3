### Ticket 04: [04-agent-core.md](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/.scratch/live-agent/tickets/04-agent-core.md)
- **Status:** delivered-for-review
- **Line counts vs caps:**
  - [pipeline/src/voice_pipeline/agent_tools.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/src/voice_pipeline/agent_tools.py): 59 / 65
  - [pipeline/src/voice_pipeline/agent.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/src/voice_pipeline/agent.py): 140 / 155
- **Proof:**
  - 1 ✅ `cd pipeline && uv run --offline python -c "from voice_pipeline.agent import Agent, TurnTap, build_llm; from voice_pipeline.agent_tools import build_tools, docs_server; print('ok')"` (`ok`)
  - 2 ✅ `cd pipeline && uv run --offline ruff check . && uv run --offline ruff format --check . && uv run --offline mypy` (`Success: no issues found in 7 source files`)
  - 3 ✅ `wc -l on the files in the table` (`199 total`)
- **Open issues:** none

### Ticket 05: [05-stress-dry-run.md](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/.scratch/live-agent/tickets/05-stress-dry-run.md)
- **Status:** delivered-for-review
- **Line counts vs caps:**
  - [pipeline/src/voice_pipeline/dryrun.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/src/voice_pipeline/dryrun.py): 165 / 182
  - [pipeline/tests/test_dryrun.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/tests/test_dryrun.py): 34 / 43
  - [pipeline/config/eval/system_agent.txt](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/config/eval/system_agent.txt): 21 / 21
  - [pipeline/config/eval/agent_prompts.jsonl](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/config/eval/agent_prompts.jsonl): 72 / 72
  - `pipeline/src/voice_pipeline/latency.py`: deleted (0)
  - `pipeline/config/eval/smart_home_prompts.jsonl`: deleted (0)
- **Proof:**
  - 1 ✅ `cd pipeline && uv run --offline pytest -q tests/test_dryrun.py tests/test_smart_home.py` (`14 passed in 1.04s`)
  - 2 ✅ `cd pipeline && uv run --offline ruff check . && uv run --offline ruff format --check . && uv run --offline mypy` (`Success: no issues found in 6 source files`)
  - 3 ✅ `cd pipeline && uv run --offline python -m voice_pipeline.dryrun --help` (`usage: dryrun.py [-h] [--limit LIMIT] [--live LIVE] [--run-id RUN_ID] [--system SYSTEM]`)
  - 4 ✅ `cd pipeline && wc -l config/eval/system_agent.txt config/eval/agent_prompts.jsonl && cmp config/eval/agent_prompts.jsonl /mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/.scratch/live-agent/skeleton/pipeline/config/eval/agent_prompts.jsonl && test ! -e src/voice_pipeline/latency.py && test ! -e config/eval/smart_home_prompts.jsonl` (`93 total`)
  - 5 ✅ `wc -l on the files in the table` (`292 total`)
- **Open issues:** none

### Ticket 06: [06-agent-server.md](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/.scratch/live-agent/tickets/06-agent-server.md)
- **Status:** delivered-for-review
- **Line counts vs caps:**
  - [pipeline/src/voice_pipeline/agent_server.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/src/voice_pipeline/agent_server.py): 103 / 114
  - [pipeline/tests/test_agent_server.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/tests/test_agent_server.py): 77 / 97
  - [Makefile](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/Makefile): 34 / 34
- **Proof:**
  - 1 ✅ `cd pipeline && uv run --offline pytest -q` (`16 passed in 1.03s`)
  - 2 ✅ `cd pipeline && uv run --offline ruff check . && uv run --offline ruff format --check . && uv run --offline mypy` (`Success: no issues found in 7 source files`)
  - 3 ✅ `make -n agent-dryrun agent-server pipeline-test` (`cd pipeline && uv run pytest -q`)
  - 4 ✅ `wc -l on the files in the table` (`214 total`)
- **Open issues:** none

---

### Files Changed
- [pipeline/src/voice_pipeline/agent_tools.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/src/voice_pipeline/agent_tools.py) (created)
- [pipeline/src/voice_pipeline/agent.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/src/voice_pipeline/agent.py) (created)
- [.scratch/live-agent/tickets/04-agent-core.md](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/.scratch/live-agent/tickets/04-agent-core.md) (updated)
- [pipeline/src/voice_pipeline/dryrun.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/src/voice_pipeline/dryrun.py) (rewritten)
- [pipeline/tests/test_dryrun.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/tests/test_dryrun.py) (rewritten)
- [pipeline/config/eval/system_agent.txt](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/config/eval/system_agent.txt) (created)
- [pipeline/config/eval/agent_prompts.jsonl](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/config/eval/agent_prompts.jsonl) (created)
- `pipeline/src/voice_pipeline/latency.py` (deleted)
- `pipeline/config/eval/smart_home_prompts.jsonl` (deleted)
- [.scratch/live-agent/tickets/05-stress-dry-run.md](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/.scratch/live-agent/tickets/05-stress-dry-run.md) (updated)
- [pipeline/src/voice_pipeline/agent_server.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/src/voice_pipeline/agent_server.py) (created)
- [pipeline/tests/test_agent_server.py](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/pipeline/tests/test_agent_server.py) (created)
- [Makefile](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/Makefile) (rewritten)
- [.scratch/live-agent/tickets/06-agent-server.md](file:///mnt/School/PhD/AI231/AI-231-ME3-agy/.scratch/live-agent/agy_work/work/.scratch/live-agent/tickets/06-agent-server.md) (updated)
