# smart_home dry run — results, 2026-10-23

Run: `make smart-home-dryrun` (47 prompts, 50 turns per model, one run, from the Philippines). Rows: `pipeline/out/smart_home_dryrun/20261023-144539/`. First-token time includes a new connection per prompt.

| Model | Exact (ok turns) | No call when one was expected | Tool syntax spoken as text (wide pattern) | Called, then no reply | False calls | First token p50 |
|---|---|---|---|---|---|---|
| Groq `llama-3.1-8b-instruct` (OpenRouter) | 27 / 48 (56%) | 18 | 8 | 0 | 1 (invented "7 AM") | 0.76 s |
| Groq `llama-3.3-70b-instruct` (OpenRouter) | 48 / 50 (96%) | 1 (asked for the message text) | 1 | 0 | 0 | 1.83 s (canonical 1.20 s) |
| Gemini `gemini-3.1-flash-lite` (OpenAI-compatible endpoint) | 45 / 48 (94%) | 2 (asked who to call / message) | 0 | **40** | 1 ("I'm cold" → 22 degrees) | 0.87 s |

## What the misses are

- **Groq 8B** writes the call as text in 8 turns (`function=smart_home>{"intent": "STOP"}`, `<LIGHT_OFF></LIGHT_OFF>`), which TTS would read aloud. In 7 turns Groq itself rejected the generated call (`tool_use_failed`), once for an invented tool `phone_call`. It often promises an action without calling ("I'll create a reminder for you"). Not usable as the tool-calling model with this prompt.
- **Groq 70B**: one leak (c17), and "five-minute timer" was sent as "1 minute" (it chose the nearest listed value instead of passing the user's value as the prompt says).
- **Gemini**: the calls are right, but after every tool result the next request fails with HTTP 400 `function_response.name: Name cannot be empty`: Pipecat's OpenAI adapter sends `role: tool` messages without the function name, which Gemini's OpenAI-compatible endpoint requires. So the model never confirms the action, and in two-turn prompts the second turn fails (`empty`). This is an integration defect, not a model one. Fix: use Pipecat's native `GoogleLLMService` (`pipecat-ai[google]`) for Gemini and re-run.

## Scorer defect (spec, the ticket-writer's)

`LEAK` in `dryrun.py` (`<function`, `"name":`, `smart_home(`) misses `function=smart_home>` without `<`, bare `{"intent": ...}` and `<INTENT></INTENT>`; it counted 2 of Groq 8B's 8 leaks. The wide pattern used above: `function\s*=|<function|"name"\s*:|"intent"\s*:|smart_home\s*[\(>]|</?[A-Z_]{3,}>|\[TOOL_CALLS\]` (case-insensitive). Re-scored offline from the saved rows.

## Caveats

One run per model; n = 48-50 turns; first-token times include connection setup; Gemini's latency is for the first (tool-call) response only.

# Prompt variants, 2026-10-23 (second run)

Changes before this run: Gemini routed through OpenRouter (`google/gemini-3.1-flash-lite`, provider Google AI Studio), which fixes the tool round trip; the wide leak pattern is now in `dryrun.py`; prompts in `pipeline/config/eval/system_v*.txt`. No quoted example in v1-v3 shares a 3-word sequence with any test prompt (checked by script). The four prompt runs ran in parallel, which inflates first-token times; compare them relative to each other only. One run per cell, 50 turns.

| Prompt | Model | Exact | No call when expected | Leaks | False calls | Called, no reply | Bad (empty/error) | First event p50 |
|---|---|---|---|---|---|---|---|---|
| v0 baseline | Groq 8B | 26/48 | 18 | 8 | 2 | 0 | 2 | 0.77 s |
| v0 baseline | Groq 70B | 49/50 | 0 | 0 | 1 | 0 | 0 | 1.09 s |
| v0 baseline | Gemini lite | 47/50 | 2 | 0 | 1 | 0 | 0 | 1.57 s |
| v1 rules | Groq 8B | 22/50 | 24 | 14 | 2 | 0 | 0 | 0.81 s |
| v1 rules | Groq 70B | 45/47 | 0 | 0 | 1 | 0 | 3 | 1.09 s |
| v1 rules | Gemini lite | **50/50** | 0 | 0 | 0 | 1 | 0 | 1.76 s |
| v2 router | Groq 8B | 7/50 | 43 | 23 | 0 | 0 | 0 | 0.79 s |
| v2 router | Groq 70B | 49/50 | 0 | 0 | 1 | 0 | 0 | 1.61 s |
| v2 router | Gemini lite | **50/50** | 0 | 0 | 0 | 0 | 0 | 2.15 s |
| v3 examples | Groq 8B | 40/48 | 4 | 4 | 3 | 0 | 2 | 0.73 s |
| v3 examples | Groq 70B | 45/49 | 0 | 0 | 4 | 0 | 1 | 1.16 s |
| v3 examples | Gemini lite | 49/50 | 0 | 0 | 1 | 0 | 0 | 2.43 s |

Findings:
- **Gemini 3.1 flash-lite + v2 router: 50/50, no false call, every action confirmed.** v1 rules also 50/50 with one call left without a spoken confirmation (c18).
- **Groq 70B** is best on the baseline (49/50; the miss is calling TEMPERATURE 22 for "I'm cold"). v1's 3 bad turns are infrastructure (connection errors, one 429), not the model. v3's examples made it act more eagerly: 4 false calls, inventing "8 AM" and "22 degrees" despite the rule.
- **Groq 8B**: structure hurts it (v2: 7/50, 23 leaks); examples help it most (v3: 40/48), but it still leaks tool syntax (4), Groq rejects many of its calls (9 `tool_use_failed` in v3), and it invents alarm times. Not usable for tool calling.
- The only remaining shared failure is "I'm cold" (a01): models pick a temperature instead of asking.
- Latency here is not decisive: parallel runs, a new connection per prompt, and Gemini now goes through OpenRouter (0.87 s p50 direct in the first run vs 1.57 s via OpenRouter here, under load). A serial, connection-reusing latency run is needed for the final pick.

# Serial latency and native Gemini, 2026-10-23

Serial run (`python -m voice_pipeline.latency`): one pipeline and connection per model, reused across 22 single-turn prompts (c01-c19, p05, n01, n02), first prompt a discarded warm-up, context replaced per prompt. Seconds p50 / p90. "Spoken" = first reply text after any tool round trip.

| Model + prompt | Exact | First tool call | Spoken, tool turn | Spoken, no tool |
|---|---|---|---|---|
| **Gemini 3.1 flash-lite, native Pipecat `GoogleLLMService` + v2** | 22/22 | **0.72 / 0.81** | **1.31 / 1.63** | **0.52 / 0.53** |
| Gemini 3.1 flash-lite via OpenRouter + v2 | 22/22 | 1.04-1.05 / 1.18-1.55 | 2.05 / 2.22-2.58 | 1.03-1.04 / 1.07-1.13 |
| Gemini 3.1 flash-lite, OpenAI-compatible direct + v2 | 22/22 | 0.73 / 1.00 | fails (see below) | 0.65 / 0.70 |
| Groq 70B + v0 | 21/22 | 1.33 / 2.41 | 2.18 / 3.56 | 0.71 / 1.68 |
| Groq 70B + v2 | 19/22 | 2.25 / 2.66 | 4.42 / 5.02 | 3.77 / 4.97 |

Native Gemini + v2 on the full 47-prompt set: **50/50 exact, 0 false calls, 0 leaks, 0 bad, every action confirmed**, first event p50 0.89 s (new connection per prompt).

Why the OpenAI-compatible direct route fails on the tool round trip: Gemini's stream opens each tool call with an empty fragment that Pipecat records as an extra call with no id or name (fixed by `GeminiDirectLLMService` dropping it), and Gemini 3.x requires the call's `thought_signature` to be sent back, which arrives in a field Pipecat's OpenAI adapter does not keep (not fixed). Pipecat's native `GoogleLLMService` handles both and sends `thinking_level: minimal` by default for this model. Native test ran in a scratch venv with `pipecat-ai[google,openai]==1.13.0`; `pipeline/` does not have the `google` extra yet.
