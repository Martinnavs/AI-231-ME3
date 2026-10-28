# Baseline router (h1b-lora-s0): GGUF evaluation and data feedback

Written 2026-10-28 for the HPC session that will generate more training data. Model: `martinnavs/granite-4.0-h-1b-router-baseline-s0` (LoRA, template-only data, seed 0), merged into `ibm-granite/granite-4.0-h-1b` and converted to GGUF on the laptop. All numbers are **one run, one seed**, greedy decoding (repeats are identical, so they add nothing). Evaluation: `finetune/eval/onit_eval.py router-ft`, llama-server `--jinja`, CPU, 8 threads. Per-turn rows are in `finetune/eval/results/` (git-ignored; regenerate with the commands in `finetune/README.md`).

## Results

| | OnIt set (77 turns; target at least 90%) | Pack (92 turns; target at least 85%) | Latency p50 / p95, OnIt | Latency p50 / p95, pack |
| --- | --- | --- | --- | --- |
| Q4_K_M (901 MB) | 69/77 (89.6%) | 73/92 (79.3%) | 0.51 s / 0.59 s | 0.57 s / 0.86 s |
| Q8_0 (1.56 GB) | 66/77 (85.7%) | 72/92 (78.3%) | 0.73 s / 0.87 s | 0.71 s / 1.12 s |

Pack scores use the corrected scorer (section 1); the first run reported 68/92 for both. Q8_0 is slower and no better: ship Q4_K_M. Latency target (p95 at most 0.8 s) holds on the OnIt set, misses on the pack (0.86 s; the pack has longer multi-turn prompts). Not measured: false-tool-call rate (target at most 3%), defer recall and precision.

## Failure analysis (Q4_K_M)

### 1. Scorer artifact on the pack (5 turns, fixed)

`none|<reply>` is scored as a call named `none`, but the pack's "no tool expected" is the empty list, so a spoken reply is marked wrong. Affected: `c17`, `a02`, `n01`, `n04`, `t03` turn 0. Fixed in `eval/onit_eval.py` (`defer_expect` accepts `none` wherever a pack turn expects no tool): Q4_K_M goes from 68 to **73/92 (79.3%)**, Q8_0 from 68 to 72/92. Pack numbers from before the fix are not comparable; re-score them (the fix is in the scorer only, so `rows.jsonl` calls are unchanged).

### 2. Document questions answered with Wikipedia (13 turns, the main real gap)

Pack ids `d01`-`d08`, `n03`, `t04` (turns 0 and 1), `x01`-`x03`. The user asks about their own documents ("what does the McCarthy corpus say about the Judge", "what is Suttree about") and the router writes `wiki_summary|Cormac McCarthy`, `wiki_summary|Blood Meridian`, `wiki_summary|Judge Holden`, `wiki_summary|The Road` and so on. The scoring rule counts `escalate` as correct for these (`search_docs` is a summarising tool, so the split router defers it). The model never learned that **a question about the user's own files, documents, notes or corpus is `escalate|tools`**, not a general-knowledge lookup.
Compound turns (`x01`-`x03`) make it worse: "turn off the lights and tell me about The Road" gives `LIGHT_OFF` plus `wiki_summary`; the right output is `LIGHT_OFF` plus `escalate|tools`.

### 3. Contact calls and messages with missing or unknown targets (4 turns)

`c16` "Make a phone call." gives `CALL|Carlo`; `b05` "Call Gary." gives `CALL|Gary`; `b06` "Message my boss." gives `MESSAGE|Boss`. The pack expects no call. The first is a **made-up argument**: the user gave none. The other two depend on a phonebook the router cannot see; check the expectation in `pipeline/config/eval/agent_prompts.jsonl` (`kind: phonebook`) before deciding what the target output is. Rule that is safe regardless: never invent an argument; with a missing required value, ask (`none|Who should I call?`).

### 4. Smaller misses

- `n02` "Tell me a fun fact about owls." gives `wiki_summary|owls`; expected plain chat (`none|...`). General chat from the model's own knowledge should not become a Wikipedia call unless the user asks about a named topic.
- `a01` "I'm cold." gives `TEMPERATURE|24 degrees`. The pack expects no action. Ambiguous statements should be answered or clarified, not turned into a device command.

### 5. OnIt-set misses (8 turns, same 8 on every repeat)

- Wrong deferral on chat: `n03` gives `escalate|code`, `n05` gives `escalate|other` (answer in chat instead); `r03` gives `escalate|tools`.
- A home command for a request meant for the hosted model: `device_status` (`s14`), `STOP` (`v04`), `LIST_REMINDERS` (`v05`), and `STOP` on turn 2 of `m02`. Check each against `eval/onit_prompts.py` before treating it as a data gap; some may be defer cases (serve and shell wording) where the router grabbed a nearby home intent.
- `w07` gives `wiki_summary|Baguio`; a web request that should defer.

## What to generate (for the next data round)

Keep the contract in `finetune/spec/tools.py`; examples are `prompt -> exact line(s)`. Prompts must not match the eval sentences (the generator's near-duplicate filter already drops them; keep it on, and regenerate with `--include-eval` only for the shipping model).

1. **Own-document and own-file questions -> `escalate|tools`** (largest gain, 13 pack turns). Vary: "what does my X say about Y", "summarize the ... notes", "find in my documents", "according to the corpus", questions naming an author, book, character, file or folder **as the thing being read**, not as a topic to look up. Include multi-turn follow-ups ("and what about the judge?" after a document question stays `escalate|tools`) and compound turns (home command line + `escalate|tools`).
2. **Contrast pairs for `wiki_summary`**: "who is Cormac McCarthy" (a general-knowledge lookup: `wiki_summary|Cormac McCarthy`) next to "what do my documents say about Cormac McCarthy" (`escalate|tools`). The model needs both sides, otherwise it keys on the proper noun.
3. **Missing or unknown required value -> a clarifying question** (`none|Who should I call?`, `none|What time should I set it for?`): calls and messages with no target or with a name the user did not give, alarms and timers with no time. Include the follow-up turn that supplies the value (then the call).
4. **Chat and ambiguous statements stay chat**: facts, greetings, thanks, "I'm cold", "it's dark in here" -> `none|<short reply>`; only an explicit request for a named topic goes to `wiki_summary`.
5. **Negative deferral targets**: chat that merely mentions code, files or reasoning without asking for work must not be `escalate|code|other|tools` (`n03`, `n05`, `r03` on the OnIt set). Pair each with a real defer example.
6. **Hosted-domain wording that sits near a home intent** (serve, shell and status wording: "is the server running", "stop the job", "list what is scheduled") -> `escalate`, with the home-intent twin for contrast (`STOP`, `LIST_REMINDERS`, `device_status` stay for the real home commands).

After training, report, per `finetune/README.md`: exact match on a held-out set **not drawn from these templates**, the OnIt set, the pack (the scorer is fixed; the 68/92 first-run figure is the pre-fix number), false-call rate, defer recall and precision, and p50 / p95 on CPU with Q4_K_M. State runs and seeds.

## Converting the adapter to GGUF: what actually worked

`finetune/README.md` gives the merge snippet and the llama.cpp commands. Three things it does not say (llama.cpp commit 8ca4060b, transformers 5.21, peft 0.21):

1. Merge with `dtype=torch.bfloat16`, `CUDA_VISIBLE_DEVICES=` empty (CPU, about a minute). Do not use `trust_remote_code`.
2. A transformers 5 `save_pretrained` writes files that llama.cpp's converter rejects. **Overwrite `config.json`, `tokenizer.json`, `tokenizer_config.json` and `special_tokens_map.json` in the merged folder with the base model's originals** (`hf download ibm-granite/granite-4.0-h-1b ...`), keeping only `model.safetensors` from the merge. Symptoms otherwise: `Tokenizer class TokenizersBackend does not exist` (tokenizer), or `tensor 'blk.5.ssm_in.weight' not found` at load (the saved config renames layer types `mamba` to `linear_attention`, and the GGUF then describes the wrong layers). Also, if the tokenizer file is missing the converter silently builds a 50,432-token vocab against a 100,352-row embedding, and the load fails with `token_embd.weight has wrong shape`.
3. The converter's requirements file downgrades transformers to 4.57 and torch to the CPU build. Use a separate environment for conversion, or accept the downgrade after merging (the merge itself is done by then).

Then `convert_hf_to_gguf.py merged --outtype f16`, `llama-quantize ... Q4_K_M`, and a one-line check: `turn on the lights` must come back as `LIGHT_ON`.
