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

## State (2026-10-28, end of the laptop session)

Built and tested on the laptop (30 tests, ruff clean, **nothing has run on a GPU yet**):
- the contract (`spec/tools.py`), the OnIt evaluation set and runner (`eval/`), the baselines;
- the **data generator** (`data/generate.py`): seeds give exact target lines; noise, multi-turn follow-ups, two-call turns, deferrals, pleasantries and missing-value questions; an optional teacher (`--teacher gemini`) rewrites a share of the wording; a near-duplicate filter keeps the 87-prompt pack and the OnIt set out of training; the split is by seed key;
- the **training script** (`train/train.py`: LoRA or full, loss on the target only, generation exact match on dev), `train/export.py` (merge, then the llama.cpp conversion steps), `train/run.sh` (background launcher; refuses GPU 6), `scripts/preflight.py`;
- the evaluation hook `router-ft` in `eval/onit_eval.py` for the fine-tuned model (it uses the short system message of `data/format.py`).

**Not tested** (needs the cluster): `train/train.py` and `train/export.py` have only been parsed, and their pure helpers (`train/utils.py`: label masking, batching) are tested with a stub tokenizer. Expect small API fixes (transformers or peft version differences) on the first smoke run; the 20-step `--smoke` run exists for that.

## Cluster session 2026-10-28: baseline trained, conversion left to do

**Result (one run, seed 0, template-only data, so a baseline):** LoRA (rank 32) on `ibm-granite/granite-4.0-h-1b`, 8,386 training examples, 3 epochs, 3,395 s on one shared A100 (GPU 5). Dev exact match **96.7% (290/300)**, eval loss 0.037 after epoch 1 and 0.023 after epoch 3. The dev set comes from the same template generator as the training data, so this is optimistic: **the OnIt set and the 87-prompt pack have not been run yet** and are the real test. Not done: teacher-reworded data (`--teacher gemini`), three seeds, GGUF export, latency.

**What the first GPU run needed** (so the next person does not repeat it):
- `train/run.sh` calls plain `python`: put the project environment first, `PATH=$PWD/.venv/bin:$PATH GPU=5 bash train/run.sh start ...`, after `uv sync --group train --group dev --group data` in `finetune/` (resolved to torch 2.14.1 with a CUDA 13.0 build, transformers 5.21, peft 0.21; `uv.lock` pins it).
- `transformers` 5 renamed `warmup_ratio`; `train.py` now passes `warmup_steps=0.03` (a float below 1 is a ratio). That was the only API fix.
- `mamba_ssm` and `causal_conv1d` are not installed (the node has nvcc 12.8, the torch build is CUDA 13.0), so the hybrid layers use the slow PyTorch fallback. It works, but uses much more memory and about 8 s per step: the default `--batch 16` ran out of memory next to another user's job, **`--batch 4 --accum 16` fits** (same effective batch of 64). Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
- The node's GPUs are shared and were at 97-100% utilisation from other users throughout; GPU 6 stays off limits.
- `train.py` prints its loss and eval lines to a block-buffered stdout, so they reach `runs/<name>.log` in chunks; the progress bar (stderr) is live.
- The adapter is in `runs/h1b-lora-s0/final`; `python train/export.py --base ibm-granite/granite-4.0-h-1b --adapter runs/h1b-lora-s0/final --out runs/h1b-lora-s0/merged` produced a 2.8 GB bf16 model (tested, CPU only, about a minute).

**Convert and evaluate on another machine.** The adapter is on the Hugging Face Hub (private): `martinnavs/granite-4.0-h-1b-router-baseline-s0` (adapter and config only, about 143 MB). It cannot be converted to GGUF as is: merge it into the base first.

```python
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base = "ibm-granite/granite-4.0-h-1b"
m = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16)
m = PeftModel.from_pretrained(m, "martinnavs/granite-4.0-h-1b-router-baseline-s0").merge_and_unload()
m.save_pretrained("merged")
AutoTokenizer.from_pretrained(base).save_pretrained("merged")
```

Use the versions in `uv.lock` (transformers 5.x, peft 0.21); `train/export.py` does the same merge from a local adapter. Then, with llama.cpp:

```
python llama.cpp/convert_hf_to_gguf.py merged --outfile router-f16.gguf --outtype f16
llama.cpp/build/bin/llama-quantize router-f16.gguf router-Q4_K_M.gguf Q4_K_M     # also try Q8_0
llama-server --jinja -m router-Q4_K_M.gguf --port 8089                     # the eval runner sends the grammar (spec/tools.py) with each request
python eval/onit_eval.py router-ft --url http://127.0.0.1:8089                    # the OnIt set
python eval/onit_eval.py router-ft --url http://127.0.0.1:8089 --set pack         # the 87-prompt pack
```

Compare with the table below, and measure p50/p95 model-call latency on the laptop CPU with Q4_K_M (target at most 0.8 s p95). The cluster session did not build llama.cpp.

What the generator gives: with plain templates only, 25,000 draws yield about 10,000 distinct examples (the rest are duplicates and are dropped), so **use the teacher** (`--teacher gemini`, key read from `~/.secrets/google-ai-studio/` at run time, never printed) to reach the size and the variety consumers need. Plain-template wording was written by one author, so the generator's own dev/test splits are optimistic; **the 87-prompt pack and the OnIt set are the independent tests**. Exact matches of the eval sentences are dropped from training (for example "turn on the lights"), which costs some common wordings; for the model that ships, regenerate with `--include-eval` after the evaluation is done.

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
| `data/generate.py`, `data/banks.py`, `data/format.py` | the generator, its word banks, and the chat format shared by training and evaluation |
| `train/train.py`, `train/utils.py`, `train/export.py`, `train/run.sh` | training, label masking and batching (tested), merge and GGUF steps, the launcher |
| `scripts/preflight.py` | what the machine can do (GPUs, CUDA, nvcc, disk, internet, libraries, Mamba kernels) |
| `tests/` | 30 tests: contract, generator, training helpers |
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

## Tasks for the cluster session (in order)

```
cd finetune
python scripts/preflight.py                      # 1. read the verdict: hybrid H-1B or the plain 1B
uv sync --group train                            #    (or the cluster's own environment; torch must match the CUDA build)
python data/generate.py --out data/generated --n 25000 --seed 0 --teacher gemini     # 2. data (needs the Gemini key; omit --teacher for a template-only set)
python -m pytest tests -q                        #    all green before training
GPU=5 bash train/run.sh start smoke --model ibm-granite/granite-4.0-h-1b --data data/generated --out runs/smoke --smoke   # 3. 20 steps
GPU=5 bash train/run.sh start h1b-lora-s0 --model ibm-granite/granite-4.0-h-1b --data data/generated --out runs/h1b-lora-s0 --seed 0
bash train/run.sh status h1b-lora-s0
python train/export.py --base ibm-granite/granite-4.0-h-1b --adapter runs/h1b-lora-s0/final --out runs/h1b-lora-s0/merged   # 4. merge, then the printed llama.cpp steps
```

Then evaluate the GGUF with llama.cpp (`llama-server --jinja`, grammar from `spec/tools.py`): `python eval/onit_eval.py router-ft --url http://127.0.0.1:8089` and again with `--set pack`; compare with the table above. Choose on dev, look at the test and pack results once, state seeds and repetitions (three seeds for the final configuration). If the hybrid's kernels will not build, train `ibm-granite/granite-4.0-1b` instead (62/92 untrained against 53/92 for the hybrid, but 1.7x slower at inference): decide by measurement. Bring the GGUF and the tables back; update `docs/LOCAL-LLM-RESULTS.md` and this file.

Open questions for the owner, to ask when they are online: which GPU indexes are free on `ai-n002` and whether another user competes; whether the node has internet (the preflight says); whether a second hosted request for the spoken answer is acceptable on turns the router defers (assumed yes: the router defers, Gemini does request 1 and request 2).

## Rules that apply here (the owner's standing agreements)

- Never read or print API keys (`~/.secrets/*`, `.env`, any `*key*` file); code reads them at run time and prints only OK or failure.
- No model on the Raspberry Pi except the Silero VAD (the professor's rule); nothing in this directory runs on the Pi.
- Commit and push only when the owner asks; never force-push, never push `main`; add files by name (not `git add -A`); gate commits on `ruff` and `pytest`; use `git -c safe.directory=$PWD` in a repository you do not own. Do not touch the owner's untracked files (`kek.txt`, `resources/building-voice-agents/`, `docs/VOICE-AGENT-EVALUATION-METRICS.md`, `docs/ECHO-AGENT-BRIEF.md`, `docs/GEMINI-ARGON-ECHO-RESPONSE.md`).
- Report the bottom line first, state how many repetitions a result rests on, and say when a setting was chosen after seeing the data (dev/test discipline: choose on dev, look at test once).
- zsh does not word-split `$VAR`; `pkill -f <text in your own command>` kills your own shell.
