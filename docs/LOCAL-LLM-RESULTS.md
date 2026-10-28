# Local LLM experiments: what was measured (2026-10-28)

Why: the owner wanted faster function calling at Gemini quality with a model hosted on their own machines (the laptop; nothing but Silero VAD on the Pi). Result: no local model matched hosted Gemini untrained; **Granite 4.0 H-1B with a one-line, grammar-constrained output is fast enough (0.39 s) but only about 59% exact**, so the next step is fine-tuning (`docs/LOCAL-LLM-FINETUNE-PLAN.md`, `finetune/README.md`).

Setup: Intel i5-1145G7 (4 cores, AVX-512 VNNI), 46 GB RAM, no GPU, llama.cpp commit `8ca4060b` (CPU build), Q4_K_M files, `llama-server --jinja -c 8192 -np 1 -fa on -t 4 -tb 4`, prefix cache on. Pack: 87 prompts (92 turns), the real tool schemas and system prompt of the agent (`pipeline/config/eval/`), scored by exact tool and slot match. Reference: hosted `gemini-3.1-flash-lite` about 100% exact (92/92 home commands, 56/58 other tools, 13/18 no-tool), first call 0.95 s p50 / 1.82 s p95 (single request). Raw rows and scripts were kept on the laptop (`.scratch/local-llm/`, not committed: 8 GB of models); the harness that can reproduce them is `finetune/eval/`.

## Round 1: five models, original pack, one run each (agy built llama.cpp and ran it inside the bubblewrap fence)

| Model | Exact | False call | Missed | Wall p50 | Decode | Cold prefill (2.4k-token prompt) |
| --- | --- | --- | --- | --- | --- | --- |
| Qwen2.5 1.5B | 22/92 | 1 | 62 | 1.1 s | 22.6 tok/s | 14.7 s |
| Qwen2.5 3B | 65/92 | 8 | 1 | 3.0 s | 12.6 tok/s | 32.6 s |
| Llama-3.2 3B | 49/92 | 10 | 0 | n/a | n/a | n/a (6 turns failed: tool-call output did not parse) |
| **Granite 4.2 3B** (2 runs) | **144/184 (78%)** | 4 | 4 | **7.2 s** (p90 9.1 s, p95 9.4 s) | 8.9 tok/s | 43.6 s |
| Qwen3.5 2B (hybrid) | cache probe only | | | | | prefix reuse worked in this build |

Findings: dense models cache the prompt prefix (second request: 85 ms instead of 42 s); a hybrid also reused it in this build. Granite 3B generates 57 tokens per tool call (a sentence before the call plus the JSON and the `say` argument). `llama-bench` decode drops from 15.2 tok/s on an empty context to 9.1 tok/s with the 2,500-token prompt loaded. It fails every multi-call turn (`mixed` 0/8, `multi-intent` 0/4).

## Round 2: Granite 4.2 3B optimisations and smaller Granite 4.0 models, one run each

| Variant (Granite 4.2 3B) | Exact | Missed | Wall p50 / p90 |
| --- | --- | --- | --- |
| baseline | 72/92 | 2 | 7.1 s / 9.1 s |
| `say` argument and its prompt paragraph removed | 70/92 | 11 | 5.0 s / 6.7 s |
| n-gram speculation (size 4, draft 6) | 73/92 | 2 | 8.7 s / 10.8 s (slower) |
| `say` removed + speculation | 71/92 | 11 | 6.1 s / 8.2 s |
| KV cache q8_0 | 72/92 | 1 | 9.2 s / 11.9 s (slower) |
| best combination (speculation), 2 runs | 146/184 | 4 | 8.8 s / 10.8 s |

| Smaller Granite 4.0 | Exact | Wall p50 / p95 | Decode |
| --- | --- | --- | --- |
| 1B (plain transformer) | 62/92 | 2.2 s / 3.4 s | 16.5 tok/s |
| **H-1B (hybrid Mamba-2)** | **53/92** | **1.3 s / 1.8 s** | 27.3 tok/s |
| 350M (plain) | 13/92 | 0.6 s | 50.9 tok/s |
| H-350M (hybrid) | 24/92 | 0.35 s / 0.51 s | 93.1 tok/s |

agy's round 2 hit its 2 h print timeout before writing its report; the numbers above come from its raw result files, and the two benchmarks it did not save were re-run by hand (`llama-bench`: prompt processing at 16/64/512 tokens is 55-85 tok/s depending on micro-batch; decode 15.2 tok/s at depth 0, 9.1 at depth 2,500). Speculative decoding and KV quantisation made the 3B slower on this CPU; none of this brings a 3B near 1 s.

## Round 3: Granite 4.0 H-1B only, one change per rung (2 runs = 184 turns each)

Dev (even prompt positions) and test (odd) halves differed by up to 19 points in exact match, so **differences under about 8 points are noise**.

| Rung | Exact | False / missed | Wall p50 / p95 | Decode tokens |
| --- | --- | --- | --- | --- |
| baseline (JSON tool calls, `say`) | 106/184 (58%) | 20 / 0 | 1.34 s / 2.14 s | 30 |
| micro-batch 64 | 106/184 | 20 / 0 | 1.33 s / 2.13 s | 30 |
| no `say` | 100/184 (54%) | 16 / 0 | 1.12 s / 1.82 s | 24 |
| forced call (`tool_choice=required` + a `no_action` tool) | 116/184 (63%) | 6 / 18 | 1.41 s / 2.14 s | 30 |
| no `say` + forced call | 108/184 (59%) | 4 / 24 | 1.15 s / 1.89 s | 24 |
| + routing rules and examples (no `say`) | 110/184 (60%) | 12 / 14 | 1.20 s / 1.97 s | 26 |
| + routing rules and examples (with `say`) | 116/184 (63%) | 14 / 10 | 1.42 s / 2.15 s | 29 |
| **one grammar-constrained line, no tool schemas** (579-token prompt) | **108/184 (59%)** | 14 / 24 | **0.39 s / 0.66 s** | **6** |

The one-line output removes the schemas from the prompt (cold prefill 3.5 s instead of 15.6 s) and cuts decoding to 6 tokens, so it is faster than hosted Gemini. Remaining errors are the confusions a 1B model makes (TIMER versus TEMPERATURE, a number sent to `set_volume`, over-correcting a rule). Prompt changes did not fix them.

## Round 4: the OnIt set and the split of work (`finetune/eval/`)

73 hand-written prompts (77 turns) for OnIt's flagship tasks. One run each:

| Run | OnIt set | Old pack (92 turns) | Latency p50 / p95 |
| --- | --- | --- | --- |
| hosted Gemini, all 18 tool schemas | 57 of 63 scored turns (90%) | **77/92 (84%)**, was about 100% with 9 tools | 0.90 s / 1.26 s (request 2: 0.91 s / 1.17 s) |
| untrained H-1B, split design, prompt v1 | 64/77 (83%) | not run | 0.38 s / 0.53 s |
| untrained H-1B, split design, prompt v2 | 54/77 (70%) | 48/92 (52%) | 0.39 s / 0.61 s |
| untrained H-1B asked to make the OnIt calls itself | 22/77 (29%) | not run | 0.43 s / 0.67 s |

Conclusions: the small model should handle only simple home and information calls and defer everything else (documents, files, shell, web, code, workflows) to Gemini; the router's score moved 13 points with wording alone, which is why fine-tuning is the next step; hosted Gemini lost accuracy on the old pack once the 9 OnIt tools were added (nine `search_docs` prompts went to `wiki_summary`), so the two document tools should be merged into one `local_search` and the prompt sharpened before the 18-tool agent is built.

## Pitfalls found

- GBNF rule names may contain only lowercase letters, digits and dashes (`t_PLAY_MUSIC` is a syntax error; the server returns HTTP 400). A test now checks this.
- A grammar that leaves the `escalate` reason free let the model write `escalate|reason`; the grammar now enforces enums and argument counts.
- Gemini 3 needs the `thought_signature` of a function call returned when the call is replayed in history: reuse the original part, do not rebuild it.
- `warm_http_options()` must be called inside a running event loop.
- IPv4-only connections to Gemini made no difference in an A/B (`docs/OPTIMIZATION-RESEARCH.md`, last section).
