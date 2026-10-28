# finetune/: train the voice agent's local router (start here on the cluster)

You are picking this up on the HPC cluster with no memory of the chat that produced it. Read this file, then `docs/LOCAL-LLM-FINETUNE-PLAN.md` (sections 1c, 3, 7, 8) and `docs/LOCAL-LLM-RESULTS.md` (what was measured). Everything needed to continue is in the repository.

## What and why

The voice agent (laptop mic, Pi loudspeaker, hosted Gemini `gemini-3.1-flash-lite` with tools) is slow on its first model call (0.95 s p50, 1.82 s p95). A small local model, **Granite 4.0 H-1B**, answers in 0.39 s p50 / 0.66 s p95 on the laptop CPU when it writes one grammar-constrained line, but is only about **59% exact untrained** (hosted: about 100%). The job: fine-tune it so it is a reliable **router** for simple requests, because the agent is for consumers and a wrong action is worse than a slow one.

**The split of work (owner's decision, 2026-10-28; plan section 1c):**

| Domain | Who |
| --- | --- |
| home commands (17 intents), time, weather, Wikipedia, Pi volume and status, chat | the local router, in one call, no hosted request |
| documents, files, shell, background jobs, web, writing code, multi-step workflows (OnIt's domain) | hosted Gemini; the router only says `escalate|tools` (or `code`, `workflow`, `reasoning`, `other`) |

So the router is trained on **28 names** (the 17 home intents, 8 information tools, `none`, `escalate`) and must defer the rest. The contract also lists the 9 OnIt tools because Gemini's schemas and some evaluation arms use them; the router never emits them.

## State (2026-10-28)

Done: the contract and its tests (`spec/tools.py`, 17 tests pass); the evaluation sets and runner (`eval/`); baselines (below); the plan and results documents.
Not done, in this order (each is a task for you): preflight script, data generator, dataset checks and split, training script, export and evaluation on the cluster. Check `git log -- finetune` for what has landed since this was written.

## The output language (the contract: `spec/tools.py`)

One call per line, `NAME|arg1|arg2`, the last argument may contain `|`, no newlines in arguments. `none|reply` is no tool; `escalate|why` defers. Examples: `LIGHT_ON`, `TIMER|45 minutes`, `weather|Baguio|tomorrow`, `get_current_time|Asia/Manila`, `none|You're welcome.`, `escalate|workflow`. `spec/tools.py` is the single source of truth: parser (`parse_line`, `parse_output`), formatter, the GBNF grammar (`grammar()`; llama-server enforces it at inference), and the OpenAI-format schemas the hosted model sees (`openai_tools()`). **Change the tool list there, then regenerate data and retrain.** GBNF rule names allow only lowercase letters, digits and dashes (a test checks it).

## Files

| Path | What |
| --- | --- |
| `spec/tools.py` | the contract (36 names: 17 home intents, 8 info tools, 9 OnIt tools, `none`, `escalate`) |
| `tests/test_spec.py` | 17 tests: round trips, pipes in the last argument, defaults, invalid lines, grammar enums and rule-name syntax, agreement with the agent's current tool schemas (`data/agent_tools_snapshot.json`) |
| `eval/onit_prompts.py`, `eval/onit_eval.jsonl` | the OnIt evaluation set: 73 hand-written prompts, with checks that accept several valid commands |
| `eval/onit_eval.py` | the runner: `gemini`, `router-split`, `router-full` backends; `--set pack` runs the 87-prompt regression pack (`pipeline/config/eval/agent_prompts.jsonl`) |
| `eval/system_*.txt` | prompts used for the untrained baselines |
| `data/agent_tools_snapshot.json` | the agent's current 9 tool schemas (OpenAI format) |
| `pyproject.toml` | dependency groups `data`, `train`, `dev` (not locked; pin on the cluster) |

## Verify the checkout (no GPU, no network)

```
cd finetune && python3 -m pytest tests -q          # or: uv run --offline --group laptop pytest finetune/tests -q -c /dev/null --rootdir finetune  (from the repo root)
python3 eval/onit_prompts.py                       # rewrites eval/onit_eval.jsonl; the diff must be empty
python3 -c "import sys; sys.path.insert(0,'spec'); import tools; print(len(tools.TOOLS), 'names'); print(tools.grammar()[:200])"
```

## Numbers to beat (one run each; the untrained local model is Granite 4.0 H-1B Q4_K_M, llama.cpp CPU, laptop)

| | OnIt set | Old pack (92 turns) | Model-call latency p50 / p95 |
| --- | --- | --- | --- |
| hosted Gemini, 18 tools | 57 of 63 scored (90%) | 77/92 (84%); about 100% with the old 9 tools | 0.90 s / 1.26 s (a second request for a spoken answer: 0.91 s / 1.17 s) |
| untrained H-1B router (split) | 54-64 of 77 (70-83%), prompt-dependent | 48/92 (52%) | 0.38-0.45 s / 0.53-0.73 s |
| untrained H-1B making the OnIt calls itself | 22 of 77 (29%) | n/a | 0.43 s / 0.67 s |

Targets (proposals the owner accepted): at least 90% exact on a new held-out set; at least 85% on the 87-prompt pack (counting `search_docs` prompts as correct when the router defers them); at most 3% of turns with a tool call when none was expected; at least 95% recall and 90% precision on deferring workflow, code and tool-summary requests; p95 model call at most 0.8 s on the laptop with Q4_K_M. State the number of runs and seeds with every result.

## Cluster facts (from the owner's ME2 project; `docs/LOCAL-LLM-FINETUNE-PLAN.md` section 5)

UPD CoE HPC, node `ai-n002.hpc.coe.upd.edu.ph`, one A100 per run, the GPU chosen by index with `CUDA_VISIBLE_DEVICES` (**never GPU 6**), jobs started with `uv run`, shared home `/mnt/jfs_hpc/home/anthony.martin.navarez/`. ME2 pins `torch==2.3.1` (cu121); do **not** reuse that environment (Granite 4.0 hybrid needs a recent `transformers`). No SLURM was seen anywhere; internet on the node is assumed. Unknown until you run `scripts/preflight.py`: GPU count and who else is using them, `nvcc`, disk quota, time limits.

## Tasks for the cluster session

1. **Preflight** (`scripts/preflight.py`): print GPUs, driver and CUDA, `nvcc`, disk, Python, internet reachability, and whether `mamba-ssm` and `causal-conv1d` import. If the Mamba kernels will not build, train the plain `ibm-granite/granite-4.0-1b` instead (it scored 62/92 untrained against 53/92 for the hybrid, but is 1.7x slower: decide by measurement).
2. **Data** (`data/generate.py`): seeds give exact target lines; a teacher model (any model not hosted on the Pi) only writes the user's wording. Mix, sizes and leakage rules: plan section 2. Hold out the 87 pack prompts and `eval/onit_eval.jsonl`; remove near-duplicates.
3. **Train** (`train/train.py`): LoRA first (rank 32, loss on the target line only), full fine-tuning as a comparison; checkpoints, resume, fixed seeds, three seeds for the final configuration. Smoke test with 20 steps before anything long.
4. **Export**: merge the adapter, convert with llama.cpp `convert_hf_to_gguf.py`, quantise to Q4_K_M (compare Q8_0), run through `eval/onit_eval.py router-split` (OnIt set and `--set pack`) with the grammar from `spec/tools.py`.
5. Bring the GGUF and the result tables back; update `docs/LOCAL-LLM-RESULTS.md` and this file.

## Rules that apply here (the owner's standing agreements)

- Never read or print API keys (`~/.secrets/*`, `.env`, any `*key*` file); code reads them at run time and prints only OK or failure.
- No model on the Raspberry Pi except the Silero VAD (the professor's rule); nothing in this directory runs on the Pi.
- Commit and push only when the owner asks; never force-push, never push `main`; add files by name (not `git add -A`); gate commits on `ruff` and `pytest`; use `git -c safe.directory=$PWD` in a repository you do not own. Do not touch the owner's untracked files (`kek.txt`, `resources/building-voice-agents/`, `docs/VOICE-AGENT-EVALUATION-METRICS.md`, `docs/ECHO-AGENT-BRIEF.md`, `docs/GEMINI-ARGON-ECHO-RESPONSE.md`).
- Report the bottom line first, state how many repetitions a result rests on, and say when a setting was chosen after seeing the data (dev/test discipline: choose on dev, look at test once).
- zsh does not word-split `$VAR`; `pkill -f <text in your own command>` kills your own shell.
