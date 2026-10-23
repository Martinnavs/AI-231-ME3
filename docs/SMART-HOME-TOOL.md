# The smart_home tool, the Pipecat subproject, and which LLM drives it

**Bottom line (2026-10-23).** The language model gets one tool, `smart_home(intent, slot=None)`, covering the 19 ME2 commands. It runs in a text-only Pipecat pipeline in a separate uv project, `pipeline/`. On a fixed set of 47 prompts (50 turns), **Gemini `gemini-3.1-flash-lite` through Pipecat's native `GoogleLLMService`, with the "router" system prompt, got 50 of 50 turns exactly right**: no wrong calls, no tool syntax in the reply, every action confirmed. It also had the lowest latency measured: first spoken reply 0.52 s p50 on plain turns, 1.31 s p50 on tool turns. Groq `llama-3.1-8b-instruct` is fast but cannot do the tool calling (26-40 of 48 depending on the prompt, tool syntax spoken as text). Groq `llama-3.3-70b-instruct` is accurate (49 of 50) but slower and less steady. One run per cell, one location (the Philippines).

## What was built

```mermaid
flowchart LR
    P["prompts (47)"] -- "user turns" --> R["dryrun.py / latency.py"]
    R -- "frames" --> A["Pipecat user aggregator"]
    A -- "context + smart_home schema" --> L["GoogleLLMService (Gemini) or OpenRouterLLMService (Groq)"]
    L -- "smart_home(intent, slot)" --> H["smart_home handler"]
    H -. "POST /api/command {intent, slots} (--live)" .-> D["ME2 dashboard"]
    H -- "result" --> L
    L -- "reply text" --> R
```

| Piece | File | What it does |
|---|---|---|
| Tool | `pipeline/src/voice_pipeline/tools/smart_home.py` | Pipecat `FunctionSchema` (`intent` enum of the 19 ME2 intents, free-text `slot`; the description lists the values each slotted intent accepts) and a handler that records the call, and with `--live URL` posts `{intent, slots}` to the ME2 dashboard's `POST /api/command` |
| Dry run | `pipeline/src/voice_pipeline/dryrun.py` | Each prompt through a fresh text-only pipeline per model; scores exact match, intent match, false calls, tool syntax in the reply ("leaks"), empty/error turns, first-token time |
| Latency run | `pipeline/src/voice_pipeline/latency.py` | One pipeline (one connection) per model, reused across 22 single-turn prompts, warm-up discarded; times first tool call, first spoken text, end of turn |
| Prompts | `pipeline/config/eval/smart_home_prompts.jsonl`, `system_v*.txt` | 47 prompts: 19 canonical, 7 paraphrases, 7 slot conversions, 3 unsupported values, 2 ambiguous, 4 unrelated, 2 double commands, 3 two-turn; four system prompts |

The ME2 dashboard already validates: unknown intent or missing slot is a 400; an unsupported value ("30 degrees") is accepted with `changed: false` and an "Ignored: ..." message, which the model is expected to pass on. So the tool passes the slot through as the model wrote it; the dry run measures whether the model writes the canonical form. The five older home tools in `src/voice_agent/mcp_servers/home.py` are untouched; the ME2 UI may become an MCP server later.

**How it was built.** Plan, design and level-3 tickets (full code skeletons, function inventories, line caps, a forbidden-additions list) in `.scratch/smart-home-tool/`; agy (Antigravity CLI, `gemini-3.8-flash-high`) transcribed them in two waves inside a bubblewrap fence (everything read-only except a work copy; `~/.secrets` and `~/.ssh` hidden; `fence.sh` in `.scratch/smart-home-tool/agy_work/`). Review: both waves byte-identical to the tickets, no deviations. The fence's canary (write inside, write outside) passed. Two lessons: commands typed with `!` in Claude Code must be in bash mode, otherwise they arrive as chat text; this network's IPv6 path to some Google front ends resets connections, so `fence.sh` retries agy's start-up check.

## Run it

```bash
make pipeline-sync                     # once: pipeline/.venv (its own lock; pipecat-ai[google,openai]==1.13.0)
make pipeline-check pipeline-test      # ruff, mypy strict, 10 tests
make smart-home-dryrun                 # 47 prompts x 3 models; MODELS=gemini-lite LIMIT=5 LIVE=http://127.0.0.1:8000
cd pipeline && uv run python -m voice_pipeline.latency --models gemini-lite:system_v2_router.txt,groq-70b:system_v0_baseline.txt
```

Keys are read at run time from `~/.secrets/openrouter/` and `~/.secrets/google-ai-studio/` and never printed. Rows go to `pipeline/out/` (git-ignored). The default system prompt is `system_v2_router.txt`.

## Why `pipeline/` is a separate project

`pipecat-ai==1.13.0` hard-pins `onnxruntime~=1.24.3` and `soundfile~=0.13.1`. In the root project that would downgrade onnxruntime 1.31 → 1.24.4 under Parakeet, Supertonic and the Pi's Silero (whose latency numbers were all measured on 1.31) and soundfile 0.14 → 0.13.1. So `pipeline/` has its own `pyproject.toml`, `uv.lock` and `.venv`, and the root `Makefile` ties both together (`check-all`, `test-all`, `pipeline-*`, `smart-home-dryrun`). The consequence: `pipeline/` cannot import the root packages (for example `doc_chat.bargein`) without taking their dependency floors; ticket 11 has to copy or relax.

## Results

### Prompt variants (full set, 50 turns per cell, four runs in parallel)

| Prompt | Groq 8B | Groq 70B | Gemini lite (via OpenRouter) |
|---|---|---|---|
| v0 baseline (6 lines) | 26/48, 8 leaks | **49/50** | 47/50 (asked "who?" for call and message) |
| v1 rules (7 numbered rules) | 22/50, 14 leaks | 45/47 (3 network errors) | **50/50** (one action not confirmed) |
| v2 router (silent 3-way decision + intent catalogue) | 7/50, 23 leaks | **49/50** | **50/50, clean** |
| v3 examples (request → intent descriptions) | **40/48**, 4 leaks | 45/49, 4 invented values | 49/50 |

Native Gemini + v2, full set: **50/50, 0 false calls, 0 leaks, 0 bad turns, every action confirmed.**

All variants keep Pipecat's recommended "spoken aloud" sentence. No quoted example in v1-v3 shares a three-word sequence with any test prompt (checked by script; the first drafts did, and were rewritten). The one shared failure is "I'm cold": some models set a temperature instead of asking. Groq 8B is confused by structure and helped by examples, but still speaks tool syntax and Groq rejects many of its calls (`tool_use_failed`). Examples made Groq 70B over-eager (invented "8 AM").

### Latency (serial, connection reused, 22 prompts, seconds p50 / p90)

| Model + prompt | First tool call | Spoken reply, tool turn | Spoken reply, no tool |
|---|---|---|---|
| **Gemini lite, native + v2** | **0.72 / 0.81** | **1.31 / 1.63** | **0.52 / 0.53** |
| Gemini lite via OpenRouter + v2 | 1.05 / 1.55 | 2.05 / 2.58 | 1.04 / 1.07 |
| Gemini lite, OpenAI-compatible endpoint + v2 | 0.73 / 1.00 | fails (below) | 0.65 / 0.70 |
| Groq 70B + v0 | 1.33 / 2.41 | 2.18 / 3.56 | 0.71 / 1.68 |

Times are from the moment the LLM gets the text; STT before and TTS after are not included. A tool turn costs two LLM requests (the call, then the confirmation).

### Why not Gemini's OpenAI-compatible endpoint

Two defects when Pipecat's OpenAI service talks to it with tools: (1) the stream opens each tool call with an empty fragment, which Pipecat records as an extra call with no id or name, and Gemini rejects the next request (HTTP 400, empty `function_response.name`); (2) Gemini 3.x returns a `thought_signature` with each function call that must be sent back, and Pipecat's OpenAI adapter drops it (HTTP 400, "Function call is missing a thought_signature"). (1) can be patched in `build_chat_completion_params`; (2) cannot from outside. The native `GoogleLLMService` handles both and sends `thinking_level: minimal` for this model by default. OpenRouter also works (it handles the signature) but adds about 0.3 s per request.

## Decisions taken

| Decision | Why |
|---|---|
| One plain Pipecat function, not an MCP server | fewest moving parts; ME2 UI may become an MCP server later |
| Slot passed through as the model wrote it | the dashboard validates; the dry run measures the model |
| Gemini `gemini-3.1-flash-lite`, native `GoogleLLMService`, `system_v2_router.txt` as the baseline fast model | 50/50 and the lowest latency measured |
| Groq 8B not used for tool calling | 26-40 of 48, tool syntax spoken aloud |
| A second, faster "ornamental" model in parallel (Groq answers instantly, Gemini does the work) not adopted for now | with native Gemini, plain replies start at 0.52 s (Groq 8B: 0.3-0.4 s); on tool turns the gap (1.31 s) is closed more cheaply by speaking an acknowledgement, or the dashboard's own result from a template, when the tool call arrives at 0.72 s; a second voice can contradict the first, doubles cost, and complicates barge-in. Revisit if the main model becomes slow; ticket 10's `delegate` covers slow work |

## Known gaps

- One run per cell; one location; first-token times in the parallel runs are inflated and only comparable within the run.
- Only text: no STT errors in the prompts, no audio pipeline, no barge-in during a tool call.
- `--live` (posting to the real ME2 dashboard) was not exercised end to end in these runs.
- The prompt set is small (47) and written by the same person who wrote the prompts; no held-out phrasing from other speakers.

Full investigation notes: `.scratch/smart-home-tool/RESULTS.md`; research reports `.scratch/speech-hosting/AGY-GEMINI-FAST.md` and `AGY-CHEAP-LLM.md` (the second relies on 2024-era models; not trusted for rankings).
