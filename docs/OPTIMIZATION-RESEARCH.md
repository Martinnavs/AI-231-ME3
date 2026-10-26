# Further latency optimizations: ten-agent research, and what survived checking (2026-10-26)

Raw reports: `.scratch/optimization-research/` (`AGY-*.md`, `CODEX-*.md`, the briefs `common.txt`, `q-*.txt`, and `run.sh`). Our own tests: `.scratch/ablations/`. The research stayed inside what `docs/INDUSTRY-COMPARISON.md`, `docs/LATENCY.md` and `.scratch/latency-research/SUMMARY.md` did not already cover. **Landed 2026-10-26:** candidate 1 (connection keep-alive, below); the rest is research and measurements.

## Bottom line

1. **Largest finding: the Gemini client goes cold between turns, and keeping its connection alive saves about 0.25 s at the median and removes most of the tail.** Interleaved A/B, 15 s idle before each request, 12 pairs: default client first chunk p50 **1,095 ms**, p90 **5,919 ms**, max 9,166 ms; with a 300 s keep-alive p50 **835 ms**, p90 **999 ms**, max 4,891 ms; paired median difference **270 ms**, default slower in 10 of 12 pairs. **Landed** in `build_llm` (`KEEPALIVE_S = 300`, agent server option `--keepalive SECONDS`, 0 = old behaviour) and confirmed through two real hedged agent servers side by side, 12 plain requests each, 15 s apart, interleaved: old behaviour first text p50 **1,269 ms**, p90 **5,871 ms** (three requests at 5.9-6.1 s); new behaviour p50 **842 ms**, p90 **1,155 ms**, max 2,797 ms. So even with two requests racing, the cold connection produced multi-second stalls. The cluster at 5.9-6.1 s looks like a timeout plus one retry somewhere in the stack, but Pipecat's own retry is off by default (`retry_on_timeout=False`), so the mechanism is not identified. The R1 probe sent one request a minute and always met a cold connection, so its p90 of 2.86 s and its 7-12 s maxima probably include this; rerunning the probe warm would say how much.
2. **The industry comparison needs a correction.** Real production medians are 1.3-1.7 s, not 0.6-0.9 s (verified below). Our plain turns (2.34 s) are about 1 s above them; our tool turns (2.84 s) sit in the middle of published tool-workflow means (1.27-3.08 s).
3. **The 100 ms capture block costs about 40 ms**, found independently by five of the ten reports and confirmed in our code (the pause detector is fed once per block; the "0.6 s" hold measures 0.64 s).
4. **Pre-rendered confirmations** (both engines) could take about 0.3 s off fast-path commands.
5. **The laptop was on battery in the `balanced` profile (governor `powersave`) when checked.** Our own handoff says to run `scripts/power_mode.sh max` on AC before measuring. If the spoken sessions ran like that, the STT and TTS times are inflated. This needs the owner's go.
6. **Several tempting ideas failed our checks** (list below): a streaming-ASR swap, speculative LLM start from Kroko's text, a shorter hold, delayed hedging, Silero hysteresis, cloud TTS.

## How it was run

Five axes along the path of one request (MECE), each given to one agy agent (`gemini-3.8-flash-high`, plan mode, search only) and one codex agent (`gpt-6-luna`, reasoning `xhigh`, read-only sandbox, live search), independently, with the same brief: the system, the numbers, a list of what is already covered, rules for evidence, and a sub-question checklist per axis. Axes: (1) audio capture, transport, playback; (2) end of turn and STT; (3) LLM and agent; (4) TTS; (5) perceived latency and measurement.

| Axis | codex | agy |
|---|---|---|
| 1 audio | done | failed twice (a domain I added was not in agy's allow-rules; a network reset), done on the third run with the original domain list |
| 2 turn and STT | done | done |
| 3 LLM | done | failed once (network reset), done on retry |
| 4 TTS | done | done |
| 5 experience | done | failed once (network reset), done on retry |

**Reliability.** Codex was careful: it marked what it could not find and corrected two of our own notes. Agy gave larger savings with less evidence, and several of its claims failed checks (below); its model benchmark tables are from 2024. Findings that both engines made independently are the most reliable: capture block size (five reports), pre-rendered audio, ORT thread tuning, Pocket TTS as a candidate, cloud TTS not worth it, adaptive hold, TEN VAD, Moonshine v2, dynamic hotwords.

## The industry numbers, corrected (all verified on the pages, 2026-10-26)

| Source | What it measures | Numbers |
|---|---|---|
| OpenBenchmarks via BenchLM (snapshot 2026-10-19) | real phone calls, platform level, time to first audio byte, median / p95 | Telnyx 1,296 / 1,856 ms; ElevenLabs 1,424 / 1,768; Bland 1,520 / 2,248; Vapi 1,558 / 2,008; Retell 1,740 / 2,259 |
| Cekura voice-agent workflow benchmark (updated 2026-10-22) | the same agent on the same calls, 246 calls per platform, **mean** response time, tool workflows | ElevenLabs 1.27 s; GPT Realtime 1.58; Telnyx 1.82; Pipecat 1.97; Retell 2.21; PolyAI 2.46; LiveKit 2.59; Vapi 3.08 (Gemini Live about 3.05 per codex, not re-read) |
| Daily, "Advice on building voice AI" (June 2025) | p50 budget of the best hosted services | network 200 + turn detection and transcription 400 + LLM 500 + TTS 200 ms; "any conversation turn with a tool call doubles the LLM latency" |
| LiveKit eot-bench | end-of-turn delay at a 5% false-cutoff budget (English) | best 647 ms (Soniox); ultraVAD 899; Gradium 913; AssemblyAI 1,049; SmartTurn v3.2 1,051 (35.2% false cut-offs at 300 ms); LiveKit v1-mini 1,070 |

Caveats: the phone numbers are platform-level and include the carrier path; Cekura's means do not split tool and plain turns; "good 0.6-0.9 s" from the course notes is an aspiration, not a published median. The eot-bench result also says our 0.64 s hold is already at the fast end for end-of-turn detection.

## Tested here

| Test | Result |
|---|---|
| **Idle Gemini connection**, default client (aiohttp, 15 s keep-alive), first chunk after 1 / 8 / 20 / 40 s idle, n=5 each | p50 837 / 986 / 995 / 1,014 ms; max 1.4 / 3.2 / 8.0 / 5.8 s |
| Same, keep-alive 300 s | p50 767 / 582 / 734 / 825 ms; max 1.6 / 0.9 / 1.0 / 1.0 s (sequential run, so order effects are possible) |
| **Interleaved A/B at 15 s idle**, n=12 pairs | default p50 1,095, p90 5,919; keep-alive p50 835, p90 999; paired median difference 270 ms; default slower in 10 of 12 |
| **Speculative LLM start from Kroko's text** (27 real turns) | Kroko's text at the first 0.3 s pause equals the final text on 22% of turns (21% word edits); from it the LLM makes the identical tool call on 14 of 27 (52%; 56% of the 25 turns that had their tools in this offline run); 8 differ in arguments, 3 in the tool. Run-to-run model variation not measured |
| **End-of-turn hold sweep** (Pi-mic recording, 14 phrases) | phrases cut in two: 2 at 0.6 s, 3 at 0.5 s, 7 at 0.4 s, 9 at 0.3 s |
| **Early decode** (earlier ablation, 43 real turns plus two labelled sets) | about 1% of words change on real turns; saves about 0.3 s |
| **Demo names in the entity list** (earlier ablation) | fixes every name error except "Dune"; 9 of 43 turns change, all fixes |

## Candidates, ranked

Saving figures are my judgement for our setup unless the row says measured. They overlap (several attack the same dead time), so do not add them up. "Owner" means it needs a decision or access from the owner.

| # | Idea | Source | Claimed | Realistic for us | Effort | Owner |
|---|---|---|---|---|---|---|
| 1 | **Keep the Gemini connection alive (LANDED, see bottom line)** (aiohttp session with `keepalive_timeout=300` through `HttpOptions(aiohttp_client=...)` in `build_llm`; also try a ping every few seconds if the server side closes early) | agy 3 (idea); measured here | 320 ms | **about 250-430 ms median, tail 5.9 s -> 1.1 s (measured twice, n=12 each)** | done | done |
| 2 | **Run sessions on AC with `power_mode.sh max`** | agy 1; our handoff | 30-60 ms | unknown: STT finalise (0.37 s) and TTS (0.35 s) may shrink; measure both modes | zero | changes a laptop setting |
| 3 | **Capture blocks 100 -> 20 ms** (`MicHub.block_ms`, `listen_turn`) | codex 1, codex 2, agy 1, agy 2, agy 4 | 40-80 ms | about 40 ms (the 0.64 s hold shows the quantisation) | low | no |
| 4 | **Pre-render fixed confirmations** at startup ("The lights are on." and the other fixed fast-path lines; the 65 filler lines already work this way) | codex 4, agy 4 | 350 ms | about 0.3 s on fast-path commands (first sound 1.77 -> about 1.45 s); variable lines (timers, volume numbers) stay synthesised | low | no |
| 5 | **More local answers with no LLM** (time and date; later the home forecast refreshed in the background, the rest of R5) | agy 3 (inject into prompt), my adaptation | 1,050 ms | the whole LLM turn on those questions (1.5-2 s) | low | no |
| 6 | **Replay the LLM choice from Manila**: newer Gemini models (3.8 Flash and others exist; 3.1 Flash-Lite is not in Google's caching table), Claude Haiku 4.5, GPT-4.1 mini, hosted open models, with our real prompt and tools, first call and tool accuracy | codex 3 | unknown | the LLM is 49% of the wait; this finds whether any model beats 1.05 s | medium, cents | keys for non-Google providers |
| 7 | **Early decode end to end** (`--early-decode-ms 200`) | earlier ablation; agy 2 | 300-370 ms | about 0.3 s | low | no |
| 8 | **Adaptive hold for closed commands** (end the turn early only when the partial matches a fast-path phrase) | codex 2, agy 2 | 0-150 / 440 ms | 0.2-0.3 s on commands only; risk: "turn on the lights... in the kitchen" | medium | no |
| 9 | **TTS tuning**: ORT threads for the first chunk, `total_steps` 2-3 on the first chunk, INT8 weights (`askurios8/supertonic-3-int8` exists on Hugging Face) | codex 4, agy 4 | 25-80 ms each | 50-100 ms together (unmeasured); quality check needed | low, offline | downloading a model |
| 10 | **Prompt and schema size** against first-call time | codex 3 | unknown | measure our own slope; no published ms per 1,000 tokens exists | low, cents | no |
| 11 | **Pi playback settings** (stream latency, start threshold, keep the stream open) | codex 1, agy 1, agy 4 | 50-250 ms | at most about 35 ms by our player's own comment; amplifier wake is device-specific | low | access to the Pi |
| 12 | **Demo names in the entity list** | earlier ablation; agy 2 | accuracy | removes repeat questions (each costs a whole turn) | low | changes what the speech server hears |
| 13 | **Local router for paraphrased home commands** (small embedding classifier, or a function-calling model such as FunctionGemma) | agy 3, codex 3 | 800-1,050 ms | only for commands the regex misses; accuracy unproven on our 17 intents | high | no |
| 14 | **Benchmark candidates**: Moonshine v2 streaming (models exist on Hugging Face; the sherpa-onnx catalogue had only v1 at our last check), Nemotron streaming, Pocket TTS and Piper (both engines named them; Pocket TTS claims about 200 ms to the first chunk and about 2.4x real time on a 4-vCPU x86 CPU) | codex 2/4, agy 2/4 | 0-450 ms | only worth it if 1-9 are not enough | high | no |
| 15 | **Measurement**: per-hop timestamps, a replay harness (N >= 100 paired utterances, bootstrap intervals), and a readiness check that the Pi speaker really plays (a tone heard by the laptop microphone) | codex 5, agy 5, codex 1 | 0 ms | makes every decision above honest; we have 27 turns | medium | no |

## Checked and set aside

| Idea | Why |
|---|---|
| Swap the final STT for a streaming Zipformer (agy 2's first idea, "WER maintained") | Our own table (`docs/STT-HOSTING.md`): Kroko 21.8% WER on the hard set and 47% entity recall against about 5% for Parakeet |
| Speculative LLM start on Kroko's early text | the only check available before the final call is text equality, which holds on 22% of turns |
| Shorter hold, including agy 1's "20 ms blocks allow 400-450 ms" | unsupported; the benchmark shows the best models need 0.65-1.15 s at a 5% budget; our replay cuts phrases at shorter holds |
| Delayed hedging as a "correction" (agy 3 and 5) | it saves tokens but gives no p50 gain by design (codex 3 says the same); both-at-once costs about 1-2 US cents per hundred turns and is the reason p50 improved |
| Silero trailing delay (agy 2) | our end of turn uses the loudness-based pause detector, not Silero |
| "A short first sentence saves 360-480 ms" (agy 4/5) | our ticket 25 measured only 0.06 s of waiting for the first sentence to end; TTS chunk-size gain is 0.07-0.1 s |
| Cloud TTS, Opus, DSCP/QoS on the cable, Wi-Fi power-save, an AEC swap | no gain from Manila (both engines), or irrelevant on an Ethernet cable, or no effect on first sound |
| OpenVINO (agy 2: 170-210 ms on the iGPU) | codex found no evidence; the installed ONNX Runtime has only CPU providers, so it needs another package |
| Forced tool mode `ANY`, the 2024 provider benchmark tables, FunctionGemma "58% to 85%" | unverified or stale; the FunctionGemma figures were not on the page I fetched |
| Artificial Analysis "Gemini 3.1 Flash-Lite 4.56 s" | true on its page, but for a 10,000-token workload; we measure 1.0-1.3 s with about 1.5k tokens, so rankings must be replayed on our prompt (candidate 6) |
| Speech-to-speech on the CPU | none exists; cloud ones cost more and bring the echo problem back |

## Corrections to our own notes that the research suggests

- `docs/INDUSTRY-COMPARISON.md`: the "2-3x slower than the industry" headline overstates the gap; see the corrected table above.
- The "implicit caching dropped" note: Google's caching table does not list Flash-Lite at all (its minimums are 4,096 for 3.8 Flash, 3.6 Flash and 3.1 Pro; 2,048 for 2.5), so our small prompt may or may not qualify; low priority.
- `docs/LATENCY.md` still quotes 0.15-0.2 s for the smaller first chunk; the later estimate is 0.07-0.1 s.

## Not found anywhere

Tool-turn versus plain-turn latency on one production stack; acceptable-delay thresholds by task type, fixed versus variable delay preference, and Taglish filler effects; head-to-head CPU numbers for the candidate STT and TTS models on this i5; any Gemini-specific measurement of connection warm-up from the Philippines (candidate 1 supplies one); how long Google's front end keeps an idle connection (our test shows the client side matters at 8 s and above).

## Proposed order of work

1. Candidate 1 is done; rerun the LLM probe warm to re-baseline the provider tail, and consider Pipecat's `retry_on_timeout=True` with a short `retry_timeout_secs` (a second chance for a stuck first chunk inside one pipeline; untested).
2. Candidates 3 and 4 and 5: all small, none risky, all on the command path.
3. Candidate 2 and 7 together in one spoken session on AC power (compare against the numbers in `docs/INDUSTRY-COMPARISON.md`).
4. Candidate 6 once the connection is warm, so the comparison is fair.
5. Candidate 15 in parallel, so steps 3 and 4 can be judged on more than 27 turns.
