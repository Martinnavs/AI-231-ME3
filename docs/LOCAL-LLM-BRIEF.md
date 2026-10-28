# Voice-agent demo: full context for a local-LLM review

This document is standalone. You do not need access to the repository. Everything you need to judge whether a local LLM can replace the hosted one is here. Numbers marked "estimate" were not measured.

## 1. What we are asking you

You previously proposed a local LLM stack for this machine (two documents: a model/stack recommendation and a `llama-server` tuning guide). Our goal is:

> Do function calling **faster** than the hosted model, at **the same tool-calling quality and general answer quality** as `gemini-3.1-flash-lite`, hosted on our own machine.

Please read the facts below and answer:

1. Given the measured latency budget (section 5) and the token counts (section 6), can any local model beat the hosted first-call time of about 0.9 s on this CPU while keeping tool accuracy? Show the arithmetic (prefill tokens, decode tokens, tok/s you assume, and why).
2. Which concrete model and build would you test first, and what result would make you drop it?
3. Our concerns about your earlier suggestions are in section 9. Please respond to each: correct us where we are wrong, and withdraw suggestions that do not hold.
4. If a local model cannot win on all turns, is there a smaller role (for example, a router for home-control commands only) that would pay off given the traffic mix in section 7?

Please separate what you know from sources (give links) from what you are estimating.

## 2. The system

A demo voice agent for a university project. The user speaks to a laptop microphone. A Raspberry Pi with a loudspeaker, connected to the laptop by an Ethernet cable, plays the replies.

```
laptop mic -> echo cancellation + ducking -> STT server (laptop)
    -> agent server (laptop, Pipecat + hosted LLM + tools)
    -> TTS server (laptop) -> audio over Ethernet -> Pi loudspeaker
```

- **STT:** a local streaming recognizer (Kroko) gives live partial text. A local Parakeet-TDT model re-decodes the finished utterance with hotword boosting and a name-repair step. Both run on the laptop CPU.
- **TTS:** Supertonic, local, on the laptop CPU. Common short replies (confirmations, fillers) are pre-rendered and cached.
- **Pi:** plays audio and exposes a small device tool server (speaker volume). Rule from the course professor: **no model may run on the Pi except the Silero VAD.** So any local LLM must run on the laptop.
- **Agent server:** Pipecat 1.13 with its native Gemini service (`GoogleLLMService`), model `gemini-3.1-flash-lite`, plus tools over MCP. One WebSocket per turn from the client.
- **Everything local shares the laptop's 4 cores:** STT (two models), TTS, echo cancellation, VAD, the agent server and the client loop. A local LLM would have to share them too.

## 3. The laptop (measured)

| Item | Value |
| --- | --- |
| CPU | Intel i5-1145G7, 4 cores / 8 threads, AVX2 + AVX-512F + AVX-512 VNNI |
| RAM | 46 GiB visible (48 GB configuration); DIMM channel layout not verified |
| GPU | Intel Iris Xe iGPU (shares system memory); no Vulkan, OpenCL or SYCL tooling installed |
| CPU governor | `powersave` (a power-profile change was tried on battery and rejected; an AC-power repeat is pending) |
| Installed | no `llama-server`, no `ollama`; nothing downloaded for a local LLM |
| OS | Arch Linux, kernel 7.2 |

## 4. The hosted model and tools

- **Model:** `gemini-3.1-flash-lite` over the native Gemini API. Connections are kept alive (idle limit raised from the SDK default of 15 s to 300 s), and a silent warm-up request is sent at startup. These two changes cut median first-call time from about 1.1 s to about 0.84 s and the 90th percentile from 5.9 s to 1.0 s (interleaved A/B).
- **Hedged requests:** each LLM request is sent twice in parallel and the first to produce a tool call or text wins. A local model could not be hedged this way.
- **Tools** (about 1.5k tokens of system prompt plus tool schemas in total):
  - `smart_home`: lights, music, volume, timers and similar home commands. Side effects, so only one request may run it.
  - `weather`: Open-Meteo forecast plus place search (HTTP).
  - `clock`/time: answered locally for "what time is it" style questions.
  - `wikipedia`: lookup.
  - Pi device volume: reached over the cable.
  - The model may also emit a short spoken `say` argument with a tool call (a spoken acknowledgement while the tool runs).
- **Regex fast path:** 13 kinds of simple commands ("turn on the lights", clock questions) skip the LLM entirely and are answered in about 1.0 s end to end. This already covers about 14% of real turns.
- **Tool results:** on tool turns we ask the model for no second request after the tool runs (the tool's spoken result is used directly). So the LLM is called once per tool turn.
- **Filler audio:** a stall filler is played if nothing is ready after 1.8 s, with 1.5 s between fillers, and a give-up at 8 s.

## 5. Latency: what we measure and where the time goes

End to end is measured from the end of the user's speech (final transcript) to the first audible audio at the speaker. Spoken sessions with a real microphone, pooled after the latest fixes, n = 38 turns:

| Statistic | Value |
| --- | --- |
| median | 2.29 s |
| p90 | 2.98 s |
| p95 | 3.14 s |

By turn type: regex fast path about 1.0 s; clock answers 1.3-1.4 s; first turn of a session 1.6 s; tool turns about 2.5 s.

Where the time goes on a tool turn (approximate):

- The pause rule that decides the user has finished: 0.6 s after the last voiced audio. This is a fixed cost that no LLM change affects.
- Final STT re-decode and name repair: a few hundred ms.
- Hosted first model call (the call that returns the tool call): about 0.9 s p50 (single request p50 0.98 s, p90 1.3 s in the warm probe; hedging brings p50 to about 0.84 s).
- The tool itself: the weather lookup is the largest item, **1.3-4.2 s** after the model's call. Open-Meteo drops idle connections after 3-5 s, so it cannot be kept warm.
- TTS first audio and the network hop to the Pi.

Industry reference points we compared against (from public benchmark pages, checked 2026-10-26): phone voice-agent medians of 1.30-1.74 s (p95 1.77-2.26 s) in one benchmark; tool-workflow means of 1.27 s (best) to 3.08 s (worst), with Pipecat at 1.97 s, in another. Our assessment: mid-pack for agents with tools; an honest floor for our architecture is about 1.8-2.0 s.

## 6. Prompt and output sizes the LLM must handle

- **System prompt + tool schemas:** about 1.5k tokens, static, identical every turn.
- **Per-turn input:** the user utterance (about 5-30 tokens) plus a short conversation history (a few hundred tokens at most in a demo).
- **Output on a tool turn:** a function name plus JSON arguments, about 25-40 tokens, sometimes with a `say` string.
- **Output on a plain answer:** one to three spoken sentences, streamed to TTS sentence by sentence. The first sentence must arrive fast.

## 7. Real traffic mix

From 69 agent turns in two spoken sessions on 2026-10-27:

| Turn type | Share |
| --- | --- |
| other tools (weather, wikipedia, time zones, Pi volume) | 51% |
| plain answers, no tool | 25% |
| regex fast path (no LLM) | 14% |
| home commands that reach the LLM | 10% (7 turns, six of them music or volume) |

A faster router for home commands only would, at an assumed 0.4-0.5 s saving (a guess), save about 0.04 s per turn on average.

## 8. Quality bar: what "same as Gemini" means here

We run an 87-prompt dry run (twice per prompt) against the real tool schemas and score the exact tool and arguments:

| Slice | Prompts | Hosted model result |
| --- | --- | --- |
| expects only `smart_home` | 46 | 92/92 exact |
| expects other tools or a mix | 32 | 56/58 |
| expects no tool | 9 | 13/18 (72%) |

The "no tool" slice is where the hosted model is weakest: knowing when not to call a tool. A replacement must not do worse there, and must not invent tool arguments.

Evidence on small models so far (none measured on this laptop with our tools):

- Groq-hosted `llama-3.1-8b` is fast but wrote its tool calls as plain text: 26-40 of 48 correct on our earlier set.
- FunctionGemma 270M: Google reports 58% base and 85% fine-tuned on its own mobile-actions test, not our intents.
- Qwen3.5 0.8B and 2B: BFCL-V4 25.3 and 43.6. A third-party llama.cpp report on an i7-10700 gives 45.8 tok/s decode and 237 tok/s prefill for the 0.8B, and 22.4 and 114 for the 2B. We expect roughly half of that on this laptop (estimate).
- Gemini's OpenAI-compatible endpoint did not work with tools in Pipecat (an empty phantom call, a dropped `thought_signature`), so the native Gemini service is used. A local model would be served through an OpenAI-compatible `llama-server` endpoint, which is a different integration path.

## 9. Our concerns about the earlier suggestions (please respond)

1. **Speed estimates.** You gave 18-24 tok/s for Qwen3.5-4B at Q4_K_M (about 2.8 GB) on about 51 GB/s memory. The theoretical ceiling is about 18 tok/s, and real runs reach perhaps 60-70% of it. Using the i7-10700 data above, we estimate 8-12 tok/s for a 4B model on this 15-28 W laptop. Please show your source or derivation for 18-24.
2. **Tool-call arithmetic.** At 25-40 output tokens and 8-12 tok/s, decode alone is 2-4 s, plus 0.5-1 s to prefill the new turn. The hosted first call is about 0.9 s in total. Even with a 2x speculative-decoding gain we do not see a local 4B beating it. Only 0.3-2B models look fast enough, and those are the ones with weak tool accuracy.
3. **Prompt caching on hybrid models.** Qwen3.5/3.6 use Gated DeltaNet (recurrent) layers and LFM2.5 uses convolution layers. As we understand it, llama.cpp cannot trim or shift recurrent state, relies on rolling checkpoints instead, and has had checkpoint-restore bugs that force full re-processing of the prompt. A full re-process of our 1.5k-token prompt costs seconds. Your claim of "0 ms on subsequent turns" and the `--cache-reuse` advice may not apply to your own recommended models. Is a plain dense model (for example Granite 4.2 3B) the only safe choice for caching?
4. **Internal contradictions.**
   - You advise keeping the footprint under 16 GB for dual-channel memory, then recommend a 23 GB model.
   - You advise prefill on the iGPU and decode on the CPU, but llama.cpp assigns each layer to one device, so we do not see how to split by phase. Your second document then says full iGPU offload is slower.
   - Thread counts differ (3 in one, 2 in the other).
5. **Possibly outdated or incorrect flags.** `Q4_0_4_8` and `--repack` (online repacking is now the default; the flag is `--no-repack`); `--lookup-ngram-min` and a per-request `"lookup": true` on `llama-server` (prompt lookup was a separate example binary, newer builds changed this). Please give the exact current flags and the build you tested.
6. **Shared CPU.** Our STT (two models), TTS, echo cancellation and agent server already use the same four cores. `nice -n -15` needs root. Your plan assumes the LLM has spare cores. How would you budget this?
7. **Replacing STT/TTS.** You suggested Moonshine and Kokoro/Piper. Our measured STT comparison already ruled out smaller streaming models for accuracy (a streaming Zipformer showed 21.8% word error rate on our test set). We are keeping Parakeet/Kroko and Supertonic unless you have a measured comparison on our kind of audio (near-field laptop mic, Filipino-English names and place names).
8. **Endpointing is excluded from your targets.** Your stage targets sum to about 470 ms, but a 0.6 s end-of-speech pause rule and the STT re-decode come first in our pipeline.

## 10. Already tried and dropped (do not re-suggest without new evidence)

- Streaming-ASR swap to a smaller streaming model (accuracy too low).
- Starting the LLM speculatively from partial (Kroko) text.
- Shorter capture blocks (100 ms to 20 ms): measured, small effect, dropped.
- A shorter end-of-speech hold, delayed hedging, Silero hysteresis changes, a short first sentence to cut time to first audio (claimed 360-480 ms, not reproduced).
- Cloud TTS.
- Other hosted models (`gemini-3.5-flash-lite` tested, not better).
- Battery power profile change.

Still open on our side (so you need not propose them): a cache for weather and place lookups, a background home-forecast refresh, earlier final decode, a wider regex fast path, and a Philippine-name entity list for STT.

## 11. Constraints for any proposal

- Runs on the laptop only; nothing but Silero VAD on the Pi.
- Must keep tool-call correctness on the 87-prompt set at least equal to the hosted model, including the no-tool slice.
- Must not make the median end-to-end time worse than 2.29 s or the p90 worse than 2.98 s.
- No secrets, keys or private data should be sent anywhere as part of testing.
- The result must be measurable: give us the exact build, flags, model file, and a short benchmark (decode tok/s, prefill tok/s for a 1.5k-token prompt cold and warm, tool-call accuracy) we can run in under an hour.
