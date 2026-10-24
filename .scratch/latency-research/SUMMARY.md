# Latency research: what holds up (2026-10-24)

Three search-only agy runs (`AGY-A-tools.md`, `AGY-B-preemptive.md`, `AGY-C-gemini.md`; C's file holds two overlapping copies of the same report) plus the repository's own measurements, with the key claims checked by the assistant against the installed Pipecat 1.13 source (`pipeline/.venv`). Baseline from live sessions 1-2: end of speech to first sound 3.2-4.9 s, median about 3.7 s = end-of-turn wait 0.5-0.65 s + speech server finalize ~0.5 s + LLM first spoken text 1.5-2.4 s + TTS first audio 0.37-0.71 s + network/Pi ~0.1-0.2 s.

**The main fact: every answer is a tool turn.** Document questions call `search_docs`, commands call `smart_home`, so every answer pays two LLM requests. Isolated text measurements (`docs/SMART-HOME-TOOL.md`): tool call at ~0.72 s, plain reply first text ~0.52 s, tool turn ~1.31 s. Before the agent, `doc_chat` retrieved locally (BM25, <1 ms) and made one request: answers started 2.3-2.6 s after the user stopped (`docs/LIVE-AGENT.md`).

| Idea | Verdict after checking | Expected saving | Source strength |
|---|---|---|---|
| Retrieve before the LLM call for document questions (one request) | Holds. Risks: passages on non-question turns; follow-ups ("tell me more about that") need the query from context | ~0.7-0.8 s on document answers | Own history + agy A (inference) |
| Speak at the tool call (chime or neutral filler) | Holds: `on_function_calls_started` exists in our Pipecat (checked). UX evidence favours a chime or non-committal wording over "I turned on the lights" before the result | perceived 0.6-0.8 s on commands, clock unchanged | Pipecat source; product practice (agy A, secondary sources) |
| Templated confirmation, skip the second request (`FunctionCallResultProperties(run_llm=False)`) | API exists (checked). agy A says Gemini rejects the next turn unless a synthetic assistant message is added: **unverified, test it** | ~0.5-0.7 s on commands | Pipecat source; Gemini behaviour untested |
| Start the LLM before the turn is final (Pipecat `EagerUserTurnStopStrategy`, `SpeculationGate`) | Classes exist, **but a speculative run that reaches a tool call is cancelled** (`llm_service.py` ~1744): zero gain while every answer is a tool turn. Our agent also gets text from `doc_chat`, not from a Pipecat STT, so early text would have to be sent over | 0 now; maybe 0.3-0.7 s after retrieval-first | Pipecat source |
| Early decode at the first pause (`--early-decode-ms 200`, built) | Finalize median 659 -> 375 ms, but short-command WER 4.9% -> 7.4%; tail padding may fix it (earlier agy report) | ~0.3 s | Own measurement; padding fix untested |
| Smaller first TTS chunk (ticket 25) | Holds | 0.15-0.2 s | Own measurement |
| Shorter end-of-turn hold | Industry defaults 0.2-0.5 s vs our 0.5-0.65 s; Smart Turn rejected here (cut people off) | 0.1-0.25 s, risk of cut-offs | agy B; own Smart Turn test |
| Gemini model choice | `gemini-3.1-flash-lite` already runs with `thinking_level` minimal by default (checked in Pipecat `_THINKING_DEFAULTS`). Independent TTFT numbers are similar for 2.5-flash (thinking 0) and 2.5-flash-lite; the earlier agy ranking did not hold when measured, so **measure, don't trust** | unknown | agy C (OpenRouter/AA figures) |
| Vertex AI in Singapore | **Contradicted for our model:** Pipecat's `GoogleVertexLLMService` says `global` is "the only location that serves the Gemini 3 series; the regional endpoints serve the 2.5 series". agy C's 240-340 ms cited our own prompt as its source | only with a 2.5 model; unmeasured | Pipecat source |
| Implicit caching | Dropped: minimum prefix 2,048-4,096 tokens, our prompts are far below | 0 | agy C |
| Gemini Live API | Dropped for now: echo-sensitive server VAD, higher cost, reported 2-4 s processing tails | - | agy C |

## Run D: how products hide or fake latency (2026-10-24, `AGY-D-perceived.md`)

Citations checked by the assistant: Lala, Nakamura, Kawahara, "Analysis of Effect and Timing of Fillers in Natural Turn-Taking", Interspeech 2019 (ISCA archive: fillers let a system take the turn sooner without losing naturalness; an appropriate timing window exists and depends on the filler's form); Maslych et al., arXiv:2507.22352 (2025, VR virtual agents with verbal and gesture fillers: latency above 4 s degrades quality of experience; natural fillers improve perceived response time, most in high-delay conditions). Other points are as reported by agy (mostly product docs; not each re-checked):

- Fillers work best early: claim the turn within roughly 200-500 ms of the user stopping (Lala 2019); ticket 12's `stall` at 700 ms is in the same range.
- Non-speech sounds alone confirm "I heard you" but do not reduce annoyance on multi-second waits; verbal fillers do (Maslych 2025).
- Rotation matters: Google's conversation-design guidance says to randomize acknowledgements and skip them on some turns, or the persona sounds robotic. No study found on how many variants are needed.
- Products: Alexa's Progressive Response API (up to 5 interim messages, skill turn still limited to 8 s); Vapi `request-start` / `request-response-delayed` (after a threshold, e.g. 5 s) and `bot_say` (the model writes the acknowledgement into the tool call itself, so it costs no extra request); Retell `speak_during_execution` and backchannels; Siri's "Hold on" / "Still working"; Google's same-room chime instead of a spoken confirmation.
- Device commands: templated confirmations or local fulfilment with no second model request (Alexa Brief Mode, Google local home SDK).
- Failure: Alexa's 8 s limit; products apologise and offer to retry rather than go silent.
- Targets: human turn gaps cluster at 0-200 ms (Stivers et al. 2009, English about 236 ms); over about 1 s is noticed; over 4 s degrades the experience.
