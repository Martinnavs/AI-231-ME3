# Fine-tuning the local model (Granite 4.0 H-1B) for the voice agent's tools

Added 2026-10-28. Status: **contract built and tested (17 tests); evaluation sets and baselines built (section 8); owner's decisions of 2026-10-28 recorded in section 7; data, training and export not started.** Training runs on the owner's HPC cluster, so everything it needs is prepared here first and staged offline.

## Why this and what it must reach

Measured on the laptop (idle CPU, one model call, 184 turns; `.scratch/local-llm/`, table by `analyse.py`): Granite 4.0 H-1B with a grammar-constrained **one-line output** answers in **0.39 s p50 / 0.66 s p95** (hosted Gemini: 0.95 s / 1.82 s) but is only **59% exact** (Gemini about 100%). Prompt changes (routing rules, examples, forced calls) moved it between 54% and 63%, so the gap is the model's capacity for this task, not the prompt. Fine-tuning is the standard remedy for small models: TinyAgent's 1.1B reached 80% against GPT-4-Turbo's 79% on a 16-tool benchmark after LoRA on synthetic data, and FunctionGemma 270M went from 58% to 85% on Google's Mobile Actions test ([TinyAgent](https://arxiv.org/pdf/2409.00608), [FunctionGemma](https://blog.google/technology/developers/functiongemma/)).

Proposed targets (to be confirmed by the owner; they are proposals, not derived from data):

| Measure | Target | Today (H-1B, untrained) |
| --- | --- | --- |
| Exact line match on a new held-out test set | at least 90% | 59% (on the 87-prompt pack) |
| The 87-prompt pack (held out of training) | at least 85% | 59% |
| Turns with a tool call when none was expected | at most 3% of turns | 14 of 184 turns (7.6%) |
| Escalation recall on workflow and code requests (and precision, so simple requests are not deferred) | at least 95% recall, at least 90% precision | not applicable |
| Model call time on the laptop | p95 at most 0.8 s | 0.66 s |

## 1. The contract (built)

`.scratch/local-llm/finetune/spec/tools.py` is the single source of truth: 36 names (17 home intents, the 8 tools the agent has today, the 9 OnIt-parity tools, `none`, `escalate`), their positional arguments, defaults and enums. It provides the parser, the formatter, the GBNF grammar the server enforces, and the OpenAI-format tool schemas the hosted model sees. 15 tests (`finetune/tests/test_spec.py`) check round trips, pipes inside the last argument (a shell command), defaults, rejection of invalid lines, and agreement with the schemas of the tools the agent has now. A smoke test (`finetune/smoke_grammar.py`) confirmed that llama-server accepts the generated grammar with the H-1B model and that its replies parse.

Output language: one call per line, `NAME|arg1|arg2`; the last argument may contain `|`; no newlines inside an argument. `none|reply` means no tool. `escalate|why` means the turn needs the big model.

**The local model is a router (owner's decision).** It handles simple requests: one tool call, or independent calls, whose arguments can be read off the user's words. It is trained only on those. Everything that needs planning, iteration, code or file contents, or real reasoning is deferred with `escalate|why` (why = `code`, `workflow`, `reasoning`, `other`), and the hosted Gemini runs that turn with the full tool schemas. Examples that defer: "build me a URL shortener with tests", "do a security audit of this machine", "research X and compare", "fix the failing tests", "write a script that ...". The command policy (blocked commands, path jail) is enforced by the executor, never by the model.

**Who writes the spoken answer.** The router only chooses the call. Tools whose result is already a sentence (weather, Wikipedia, volume) are spoken as is (no second request, as today). For tools whose result needs summarising (`search_docs`, `local_search`, `read_file`, `grep`, `bash` output, `fetch_content`, `web_search`), a second hosted request writes the one- or two-sentence answer from the result. So the latency gain applies to the first call; those turns still pay one hosted request after the tool.

## 1b. Coverage check against the original request (OnIt's flagship tasks, Pareto)

From `docs/ONIT-PARITY.md` section 6: the three flagship tasks, their common path and edge cases. What the router does with each:

| Flagship step (common path unless marked edge) | Router output | Then |
| --- | --- | --- |
| Docs Q&A: "what does our refund policy say about annual plans, in the policies folder" | `local_search|refund policy for annual plans|policies` | Gemini writes the answer with the cited file and section |
| Docs: read the cited section; edge: file name misheard by speech recognition | `read_file|policies/billing.pdf` | the same |
| Docs edge: nothing found; first use of a folder (indexing time) | the same call | executor reports it; no router work |
| Build an app: "build a URL shortener in Flask with tests and commit" | `escalate|workflow` | Gemini runs the loop (write, test, fix, commit) |
| Build an app, single steps: "run the tests", "commit it", "what changed" | `bash|pytest -q`, `bash|git commit -am ...`, `bash|git status` | short result, spoken from the output |
| Build an app: "write a function that ..." | `escalate|code` | Gemini |
| Build an app edge: long test run; port in use | `serve|start|tests|pytest -q`, `serve|status|tests`, `serve|logs|tests` | spoken completion notice |
| Audit: "security audit this machine and write the findings" | `escalate|workflow` | Gemini |
| Audit, single steps: "what ports are listening", "any world-writable files in /srv", "show the SSH settings" | `bash|ss -tlnp`, `bash|find /srv -perm -o+w`, `read_file|/etc/ssh/sshd_config` or `grep|PermitRootLogin|/etc/ssh` | summary |
| Audit edge: a command that needs root; a remote host | `escalate|other` or `none|reply` (the executor also refuses) | spoken refusal |
| Web: "search for X", "open this page" | `web_search|X|news`, `fetch_content|https://...` | summary |
| Anything the user says that is not a task | `none|reply` | spoken as is |

All nine OnIt-parity tools are in the spec (`bash`, `serve`, `read_file`, `grep`, `write_file`, `edit_file`, `web_search`, `fetch_content`, `local_search`); `write_file` and `edit_file` are never produced by the router (it defers with `code`). Left out on purpose, because `bash` covers them: `find_files`, `transform_text`, `github_repo`, `send_file`, `search_document`, `index_documents` (indexing is automatic on first search). If the professor counts one of them, the spec gains a name and the data is regenerated.

## 1c. Division of work: who does what (recommended; supersedes the "router also fronts OnIt tools" idea of 1b)

Today a tool turn is two hosted requests: request 1 (the model chooses the call), the tool runs, request 2 (the model writes the spoken answer from the result). Request 2 is skipped for tools whose result is already a sentence (weather, Wikipedia, volume).

| Domain | Who | Why |
| --- | --- | --- |
| Home commands, time, weather, Wikipedia, Pi volume and status, chat replies | the **local router**, in one call (0.4 s), no hosted request at all | short arguments read off the user's words; the result is a sentence; this is where the 1B model is strong |
| Documents, files, shell, background jobs, web search, writing code, workflows | **hosted Gemini**, request 1 and request 2 | needs good argument writing (commands, queries, paths) and summarising results; this is the OnIt domain, so OnIt performance is Gemini's performance |

The router emits `escalate|tools` (or `code`, `workflow`, `reasoning`, `other`) for the second row. To avoid paying the router's 0.4 s before Gemini starts, send the turn to both at once: if the router answers locally, cancel the Gemini request; if it escalates, Gemini is already running. (This replaces the second hedged request of today.)

Consequences: the router is trained on about 28 names (17 home intents, 8 information tools, `none`, `escalate`), not 36; OnIt turns lose nothing; the 0.5 s saving on the first call of an OnIt single step is given up, and kept as an option (`router-full` arm below) only if the measurements say the small model can do it.

## 2. Data (to build)

Labels come from seeds, never from a language model's judgement; only the user's wording comes from the teacher.

1. **Seeds**: for every name, scenarios with slot banks (colours, times, durations, places, contacts, file names, URLs, search queries, the curated read-only shell commands), each giving an exact target line.
2. **Teacher**: any model that is not hosted on the Pi may be used (owner, 2026-10-28). Use the strongest hosted Gemini available through the existing key, and a second teacher for diversity if a key exists; mixing teachers is useful because one model's phrasing habits would otherwise be learned. The teacher writes 20-40 natural phrasings per seed, including speech-recognition errors in the style of our 105 recorded real turns, hesitations, and indirect requests. The key is read at run time and never printed; all data is synthetic.
3. **Mix** (target about 25,000 examples): single turn 60%, multi-turn with history ("make it brighter", "and tomorrow?") 20%, independent calls in one turn 6%, no-tool and missing-value cases 15% (overlapping the above), **escalation 15%** (about a third each of `workflow`, `code`, and `reasoning`/`other`, written as the flagship tasks and their variants, so the model learns the boundary between a simple request and a workflow). Phonebook and working directory are given in a short system message and **randomised**, so adding a contact needs no retraining.
4. **Held out**: the 87 pack prompts are never trained on; training examples with near-duplicate wording (normalised n-gram overlap of 0.8 or more with a pack prompt) are removed; a separate new test set (about 600) is made from different seeds and a separate teacher run.
5. **Quality checks**: every target line parses with the spec; label balance per name; length statistics; a sample read by hand before training.

Estimated teacher cost: a few dollars (about 2-3 million tokens at flash-lite prices; an estimate).

## 3. Training (script to write; run on the cluster)

- Model: `ibm-granite/granite-4.0-h-1b` (hybrid Mamba-2, 40 blocks). Fallback: the plain `granite-4.0-1b` transformer (it scored 62/92 untrained against 53/92 for the hybrid, but is 1.7x slower).
- Method: LoRA on all linear layers (rank 32) first; full fine-tuning as a comparison. Loss on the target line only. Sequence length 512. bf16. About 3 epochs. [inference] 25k examples x 3 epochs is about 11M tokens: under an hour on one A100-class GPU.
- Stack: transformers + peft + trl, or Unsloth (it lists Granite 4.0 support; I have not confirmed the 1B size). The Mamba layers want the `mamba-ssm` and `causal-conv1d` CUDA kernels; without them training falls back to a slow path. **A 20-step smoke test on the cluster comes first** to settle which stack works.
- Checkpoints and resume, a fixed seed, three seeds for the final configuration so the result has a spread.

## 4. Export and evaluation

1. Merge the adapter, convert with llama.cpp's `convert_hf_to_gguf.py` (the same converter family produced the GGUFs we ran), quantise to Q4_K_M, and compare Q8_0 to see what the quantisation costs.
2. Evaluate with the extended scorer: exact line match, call-set match, false-call rate, escalation precision and recall, per-argument accuracy (normalised), latency p50/p95 on the laptop with the same grammar, and confidence (token log-probabilities) for a fallback gate. Report on the 87-prompt pack and the new test set, with the number of repetitions and seeds.
3. Compare with the hosted model on the same inputs. Choices are made on the dev split; the test split is read once.

## 5. Cluster preparation

What the ME2 materials show (`Presentation/ME2 Deck — Filled.pdf`, `AI-222-Machine-Exercises/ME2/README.md`, `docs/REPRODUCE-HYBRID.md`, `Makefile`, `pyproject.toml`): the **UPD CoE HPC**, node `ai-n002.hpc.coe.upd.edu.ph`, **one A100 per run** (wide model 94 min, XL 43 min; AMP, batch 16), a shared home at `/mnt/jfs_hpc/home/anthony.martin.navarez/`, the GPU chosen by index (`CUDA_VISIBLE_DEVICES`; the docs say **never GPU 6** and use GPU 5), jobs started with `uv run`, and the ME2 environment pinned to `torch==2.3.1` from the `pytorch-cu121` index. No `sbatch` or `srun` appears anywhere in ME2, so I treat the node as a shared multi-GPU machine used directly, not a SLURM queue. [inference] It also resolved the PyTorch wheels from the internet, so I assume the node has internet. [inference] Unknown: how many GPUs the node has, whether other users compete for them, the CUDA toolkit (`nvcc`) needed to build `mamba-ssm`, disk quota, time limits.

So:
- Use a **new `uv` project** for fine-tuning (its own `pyproject.toml` and lock), not ME2's `torch 2.3.1` environment: Granite 4.0 hybrid needs a recent `transformers`, and the Mamba kernels need a matching `torch` and CUDA build.
- A **preflight script** (`finetune/preflight.py`) prints GPUs (`nvidia-smi`), driver and CUDA versions, `nvcc`, free disk, Python, internet reachability to the model hub, and whether `mamba-ssm` and `causal-conv1d` import. Run it first; it decides between the hybrid and the plain 1B.
- A **launcher script** instead of SLURM: `CUDA_VISIBLE_DEVICES=<n>` (never 6), `nohup`, a log file, checkpoints and resume, and a status command. A SLURM template is written only if you tell me there is a queue.
- Stage the model files, the data and the converter snapshot in the shared home, so a run does not depend on the network after setup.

## 6. What can be done here before the cluster

In this order: (1) data generator and seed banks, (2) dataset checks and the split with the leakage filter, (3) the training script, tested on CPU for a few steps with a small model, (4) the extended scorer and evaluation scripts, (5) the offline staging script and the SLURM template, (6) a dry run of the whole chain on a 200-example dataset.

## 7. Decisions (owner, 2026-10-28) and what is still open

| # | Question | Answer |
| --- | --- | --- |
| 1 | Escalation | The local model is a **simple router**; complex workflows go to the hosted Gemini. Train on simple, single-tool tasks plus examples that defer (section 1) |
| 2 | Targets | Accepted as proposed |
| 3 | Teacher models | Any model, as long as it is not hosted on the Pi |
| 4 | Tool list vs the original request | Checked against OnIt's flagship tasks and their Pareto edge cases (section 1b); all nine parity tools are in; six OnIt tools are deliberately covered by `bash` |
| 5 | Cluster | From ME2 (section 5); the open facts are listed there |

Still open: (a) which GPU(s) on `ai-n002` are free for this, and a confirmation that there is no SLURM queue; (b) whether the final spoken answers for tools with long results may use a second hosted request (section 1: assumed yes); (c) whether the professor counts any of the six tools left to `bash` (section 1b).

## 8. The final experiment checks (built 2026-10-28; `.scratch/local-llm/finetune/eval/`)

Assumption (owner): the requirements are vague, so only two things are judged: **how well the LLM performs on what OnIt does**, and **latency**.

Three fixed test sets, none of them used for training:
1. **OnIt set** (`onit_prompts.py` -> `onit_eval.jsonl`): 73 hand-written prompts, 77 turns: documents (6), files (8), shell (14), background jobs (6), web (7), workflows (8), code (6), reasoning (4), safety (5), multi-turn (3 prompts, 7 turns), chat (6). Arguments are checked by pattern (several valid commands are accepted, for example `ss -tlnp` or `netstat -tln`), not by one exact string.
2. **The 87-prompt pack** (92 turns): the agent's current tools; the regression check.
3. A new held-out set from the data generator (when it exists), for the fine-tuned model.

Backends, one runner (`onit_eval.py`): `gemini` (the hosted model with all 18 tool schemas; also times request 2 on the turns that need it), `router-split` (the small model; scored on whether it handles its own domain and defers the rest), `router-full` (the small model asked to make the OnIt calls itself; scored on whether its own call would have been right).

Measures: exact turn match, name-only match, latency to the first call/text (p50, p95), request-2 latency, deferral precision and recall (router), per-kind tables, number of repetitions stated.

Baselines, one run each (so differences of a few turns are noise); the local model is the untrained Granite 4.0 H-1B on the laptop with a grammar that makes every line valid:

| Run | OnIt set (exact) | Pack (exact) | Latency p50 / p95 |
| --- | --- | --- | --- |
| Hosted Gemini, 18 tools | 57 of 63 scored turns (90%); workflow and code prompts are not scored for it (it has no "escalate") | **77 of 92 (84%)** | 0.90 s / 1.26 s on the OnIt set; request 2: 0.91 s / 1.17 s |
| Router, split design, prompt v1 | 64 of 77 (83%) defer/answer correctly | not run | 0.38 s / 0.53 s |
| Router, split design, prompt v2 (tuned for the pack) | 54 of 77 (70%) | 48 of 92 (52%) | 0.39 s / 0.61 s |
| Router asked to make the OnIt calls itself (full) | 22 of 77 (29%); right tool name in 33 | not run | 0.43 s / 0.67 s |

What this shows:
- **The small model should not make the OnIt calls itself** (29%), which is the evidence for the split of 1c. Deferring is much easier than doing (70-83% untrained, depending on the prompt wording).
- **Prompt wording moves the untrained router by 13 points (83% to 70%)**, which is why fine-tuning, not more prompt work, is the route to a stable router.
- **The hosted model got worse on the old pack when the 9 OnIt tools were added: 77 of 92 (84%), against about 100% with the old 9 tools.** Nine `search_docs` prompts went to `wiki_summary`, and phonebook slots slipped. This is the tool-count risk showing up. Two document tools (`search_docs`, `local_search`) overlap and the added "Case G" wording competes with "Case C"; merging the two document tools into one `local_search` and sharpening the prompt is the first thing to try. It is also an argument for the split: the router does not suffer from it.
- Latency: the router's model call is 0.38-0.43 s p50 / 0.5-0.7 s p95, hosted is 0.90 s / 1.26 s; request 2 costs about 0.9 s more on the turns that need a summary.

Known limits: the OnIt set was written by one person (me) and has 73 prompts; "reasoning" expectations are strict (Gemini answered with `wiki_summary` or `web_search` for three of four and was scored wrong, which is arguable); `safety` for the router accepts any deferral or a refusal, since the executor enforces the command policy; the latency numbers are model-only on an idle laptop; request 2 uses canned tool results of 100-300 characters.

To run: `cd pipeline && uv run --offline python ../.scratch/local-llm/finetune/eval/onit_eval.py gemini [--set pack]`; for the router start the H-1B server (see `h1b_ladder.py` for the flags) and run `python3 onit_eval.py router-split|router-full [--set pack]`.
