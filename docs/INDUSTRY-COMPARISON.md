# Our numbers against the industry numbers (2026-10-25)

One place for "how do we compare", so nobody researches it again. **The industry targets are not new research**: they come from the owner's course notes (`docs/VOICE-AGENT-EVALUATION-METRICS.md`, untracked, read-only, section numbers below) and the latency research already in the repo (`.scratch/latency-research/SUMMARY.md`, `docs/LATENCY.md`). Our numbers come from three live spoken sessions and the agent server's log.

## Bottom line

**Correction (2026-10-26, `docs/OPTIMIZATION-RESEARCH.md`): the earlier headline below overstated the gap.** The course notes' "good 0.6-0.9 s" is an aspiration. Published production numbers are higher: real phone calls (OpenBenchmarks, median / p95) Telnyx 1.30 / 1.86 s, ElevenLabs 1.42 / 1.77, Bland 1.52 / 2.25, Vapi 1.56 / 2.01, Retell 1.74 / 2.26; tool-workflow means (Cekura, 2026-10-22) ElevenLabs 1.27 s, GPT Realtime 1.58, Telnyx 1.82, Pipecat 1.97, Retell 2.21, PolyAI 2.46, LiveKit 2.59, Vapi 3.08; Daily's own p50 budget is 200 + 400 + 500 + 200 ms = 1.3 s, and "a tool call doubles the LLM latency". Our plain turns (2.34 s median) are about 1 s above the phone medians; our tool turns (2.84 s) sit in the middle of the tool-workflow means. The end-of-turn benchmark (LiveKit eot-bench) shows the best end-of-turn models need 0.65-1.15 s at a 5% false-cutoff budget, so our 0.64 s hold is already at the fast end.

**The original reading, kept for the record:** end of speech to first sound is median 2.58 s (p90 3.03 s), against the course notes' "exceptional < 0.6 s, good 0.6-0.9 s, sluggish > 1.2 s"; the cause is the cloud LLM's first response plus the end-of-turn wait, not the speech models. Where we beat or meet the targets: cutting the bot off when interrupted (1 ms against < 50-100 ms) and the fast path (1.65-1.81 s with no LLM). Where we are over: end-of-turn wait, LLM first token, TTS first audio.

A realistic target for a tool-calling Gemini agent on this setup is **about 2 s** (see the ablations below), not 0.9 s: the model's first response alone (0.9-1.5 s) already exceeds a 0.9 s total budget. The industry's 0.6-0.9 s figures describe a plain text answer with no tool call. **A later finding lowers the numbers:** the Gemini client was going cold between turns. With the connection kept alive (landed 2026-10-26) the LLM's first sign of an answer is p50 0.98 s / p90 1.31 s for one request and 0.90 / 1.09 s for the better of two, against 1.33 / 2.86 s and 1.18 / 1.94 s before (`docs/OPTIMIZATION-RESEARCH.md`). The session numbers in this document predate it; expect the plain-turn median to fall by about 0.25 s.

## Which turns the industry numbers describe (checked 2026-10-25)

**Plain question and answer, no tool call.** The course notes define the 600 / 900 / 1200 ms bands with the formula "end-of-utterance wait + LLM first token + TTS first byte + network" and the example "What is the capital of France?" (section 7). They mention tool calls once, in a use-case list (a booking agent "spending more latency on structured tool calls"), with no number. The turn-gap figures are human-to-human conversation (Stivers et al. 2009). The end-of-turn defaults (0.2-0.5 s) and the preemptive-LLM savings come from LiveKit and Deepgram documentation, again for text-only turns. **No source in the repo gives a latency target for a turn that calls a tool**; the only tool-turn figures are our own (a text-only tool turn: call after about 0.72 s, answer text after about 1.31 s).

So the fair comparison is our turns **without** a tool call. Same 27 turns, split by what the server did with them (`out/sessions/fillers-4..6` joined to the agent server log; the groups are small):

| Our turn type | n | First sound, p50 / p90 | Transcript ready | LLM first token |
|---|---|---|---|---|
| Plain LLM answer, no tool | 8 | **2342 / 2481 ms** (1708-2714) | 1020 ms | 898 ms |
| Tool turn through the LLM | 16 | 2843 / 3161 ms (2314-3371) | 986 ms | 1503 ms |
| Fast path (no LLM) | 3 | 1774 / 1811 ms | 904 ms | - |

On the like-for-like turn we are still **about 2.6 times the 900 ms "good" line** (2.3 s against 0.9 s), so the verdict stands. The gap is mostly the end-of-turn wait plus recogniser (1.0 s against the notes' EOU 0.2-0.5 s) and the LLM first token (0.9 s against < 0.5 s). Tool use adds about 0.5 s more at the median. All figures: one speaker, one room, 27 turns.

## Side by side

Ours: 27 normal turns in `out/sessions/fillers-4`, `-5`, `-6` (talk-overs and give-ups excluded; fast path included), one speaker, one room, Pi loudspeaker at 80%. Industry: course notes unless another source is named.

| Measure | Industry target | Ours (p50 / p90) | Verdict |
|---|---|---|---|
| **End of speech to first sound** (course notes section 4) | exceptional < 600 ms; good 600-900 ms; sluggish > 1200 ms; system target < 700-900 ms | **2584 / 3025 ms** (max 3371) | Over by 2-3x |
| Human turn gap (Stivers et al. 2009, in the latency research) | about 236 ms (0-200 ms cluster); over 1 s is noticed; over 4 s degrades the experience | same as above | Noticed on every turn, never past 4 s in these sessions |
| End-of-turn hold (the silence that ends a turn) | 200-500 ms window; defaults 0.2-0.5 s (`SUMMARY.md`) | **639 / 644 ms** | Slightly over; a shorter hold risks cutting people off (Smart Turn was rejected for that) |
| STT after the end of speech | delay < 50 ms (streaming); WER < 10% | server finalise **372 / 515 ms**; transcript ready **983 / 1072 ms** after speech ends (hold + finalise) | Over. WER on the benchmark clips is about 5% (4.9% short commands, `SUMMARY.md`) but demo names are misheard ("Baglio", "June" for Dune) |
| LLM time to first token | < 500 ms, throughput >= 30 tokens/s | first sign of an answer **1.05-1.3 s** (single 1.30 s, hedged 1.05 s, server log); first text 1.4 s | Over by about 2x; every answer is a tool turn on `gemini-3.1-flash-lite` |
| TTS time to first byte | < 250 ms, real-time factor < 1.0 | **352 / 384 ms** | Slightly over (laptop Supertonic); ticket 25 (smaller first chunk) would save 0.15-0.2 s |
| Voice activity detection | 15-20 ms | not measured separately | n/a |
| **Interruption: halt audio** (course notes section 5) | < 50-100 ms | cut-off to silence **1 / 1 ms** (max 3; 13 interruptions) | Meets |
| Interruption: false cut-offs by the bot's own echo | zero | 0 of 30 early cut-offs, 0 of 12 echo transcripts (two live sessions, scripted, `docs/LIVE-AGENT.md`) | Meets on scripted tests; a bare one-word "Stop." is still missed (owner decided it is not required) |
| Network round trip to the loudspeaker | RTT < 30-50 ms | Pi TCP connect 1-2 ms over the cable | Meets |
| Give-up on a stuck turn | products give up at about 8 s with an apology (Alexa's limit) | gives up at 8.0 s, filler lines spaced 1.5 s apart | Meets |

How the median adds up (the pieces sum to the measured median): transcript ready 983 ms + LLM first token 1272 ms + TTS first audio 352 ms = 2607 ms against the measured 2584 ms. **About 38% of the wait is the end-of-turn hold and the recogniser, 49% the LLM, 14% the TTS.**

## What changed the numbers this session

| Change | Effect, with what it rests on |
|---|---|
| R3 fast path (11 command kinds skip the LLM) | 1.65-1.81 s to first sound against 2.5-3 s for LLM turns (3 turns) |
| R5 one LLM request instead of two (sentence-result tools, `smart_home` commands) | dry run: spoken time of tool turns 2.05 s -> 1.57 s; weather 2.09 s -> 0.85 s (one run each) |
| R4 hedged requests (two at once, the first wins) | server log, first sign of an answer: **p50 1.30 -> 1.05 s, p90 1.55 -> 1.33 s** (single n=18, hedged n=49; both include a few warm-ups and the request held during the pause test, so the maxima 7.5 s and 4.0 s are not comparable). The earlier 44-pair probe predicted p90 2.86 -> 1.94 s |
| Stall 1.2 -> 1.8 s; fillers spaced 1.5 s | no filler on any of the 27 ordinary turns in sessions 4-6 (it played on 4 of 13 in session 3) |
| The `say` held 0.6 s and dropped when the answer arrives | a lookup answered alone, with no "looking up" line before it (session 4) |

## What would move which number (not yet done)

| Lever | Moves | Saves | Cost or risk |
|---|---|---|---|
| Shorter end-of-turn hold (0.64 -> 0.4-0.5 s) | hold | 0.15-0.25 s | more cut-offs; Smart Turn was rejected |
| Early decode at the first pause | STT finalise | about 0.3 s (finalise 659 -> 375 ms) | short-command WER 4.9% -> 7.4% |
| Smaller first TTS chunk (ticket 25) | TTS | 0.15-0.2 s | none known |
| Demo names in the speech server's entity list | WER on names | accuracy, not speed | changes what the server hears; test on clips first |
| A faster or closer LLM | LLM | unknown | `gemini-3.1-flash-lite` is already the fast one; OpenRouter's router was 3x slower; Vertex Singapore does not serve this model |

## Ablations run on 2026-10-26 (offline: recordings and the laptop speech models, no Pi, nothing loud)

Scripts and raw output: `.scratch/ablations/` (`hold_sweep.py`, `names_ablation.py`, `dune.py`, `early-decode-sessions-4-6.txt`). Every number rests on the small set named in its row.

| Ablation | Result | Verdict |
|---|---|---|
| **Shorter end-of-turn hold** (replay of the Pi-mic recording `eot-session.wav`, 14 phrases; current rule at holds 0.3-1.0 s) | A phrase is cut in two at hold 0.3 s: 9 of 14; 0.4 s: 7; 0.5 s: 3; **0.6 s (today): 2**; 0.8 s and 1.0 s: 0. Shorter holds save only the hold difference (0.1 s at 0.5 s). The laptop-mic recording gives 2 phrases and the spontaneous one 0 phrases under this scoring, so neither can be used | **Do not shorten.** Saving 0.1-0.2 s costs a second split per 14 phrases or worse. The live client rejoins a split turn ("resumed"), but each split still starts a wasted request |
| **Early decode** (`stt_early_eval.py`; hard set 59 clips, held-out 36, and the 43 real turns of sessions 4-6) | Hard set: words changed 2.5% at a 0.2 s pause (WER 4.9% -> 7.4%); held-out 0.3% (WER unchanged 8.7%); **real turns: an early start on 42 of 43, 1.0-1.3% of 306 words changed** (WER there is not meaningful: the recognised text stood in for truth). Server finalise 659 -> 375 ms on earlier measurement | **Worth a spoken test.** On conversational turns it costs about 1% of words; the loss is on short commands, which already take the fast path. Saves about 0.3 s. Not yet run end to end with the live server (`--early-decode-ms 200`) |
| **Demo names in the entity list** (43 real turns, Parakeet re-decoded offline with the server's list against the same list plus 14 demo names, hotword score 1.0) | Baguio wrong 4 -> 0; Steinbeck wrong 2 -> 0; Anne Carson 1 -> 0; Argon ("Organ") 1 -> 0. **Only 9 of 43 turns changed and all nine are fixes** (one is capitalisation, "Ways Of Seeing"). Dune stayed "June" in 3 of 4 | **Do it.** Dune needs the phrase "summary of Dune" (fixes one of three); a hotword score of 3.0 fixes Dune but injects Harry Potter words ("Quidditch") into unrelated turns, so keep the score at 1.0-2.0 and drop the Harry Potter list for the demo. In-sample: the names were chosen after seeing these turns |
| **Smaller first TTS chunk** (no new run: the measurement is in `.scratch/*/tickets/25`) | TTS first audio by chunk size on the live server: 3 words 250 ms, 4 words 283 ms, 6 words 383 ms, 8+ words about 410 ms. Our median is 352 ms, so the first chunk is usually 5-6 words | **Saves about 0.07-0.1 s, not the 0.15-0.2 s quoted earlier.** A cheaper first step: ask the model for a `say` of 4 words or fewer (today 5-9 words, e.g. "Getting a summary of the book Ways of Seeing.") |

**What the levers add up to.** Early decode about 0.3 s, a short `say` or the ladder about 0.1 s, names removing repeat questions (the retries of sessions 5 and 6 each cost a whole turn). Together about 0.4-0.5 s off the plain-turn median of 2.34 s, so **about 1.9-2.0 s, not the 1.5 s the lower end of the earlier target assumed**: the end-of-turn hold (0.64 s, which we should not cut) and the LLM first token (0.9-1.5 s) are most of what remains. The target in this document is therefore **about 2 s** unless the LLM or the hold changes.

**Not run, needs a spoken session with the owner:** hedge on against off, the `say` hold (0.6 s), the stall time, `--early-decode-ms 200` end to end, a minimal system prompt, `--level-kofn-ducked`.

## Coverage: what has been looked up, and what has not

Covered (do not repeat): the targets in the table, filler timing research (Lala 2019, Maslych 2025), Stivers 2009 turn gaps, product behaviour for slow tools (Alexa, Vapi, Retell, Siri), the LLM choice and its first-token numbers, end-of-turn defaults, and the echo problem (`docs/ECHO-RESEARCH.md`).

**Not covered (confirmed absent from every source in the repo):** published latency figures for *tool-calling* voice agents specifically (the course notes describe plain Q&A turns); speech-to-speech models other than Gemini Live (dropped for echo sensitivity and cost, `SUMMARY.md`); any measurement with a second speaker, another room or another loudspeaker distance.

## Caveats

- One owner, one room, one Pi loudspeaker; 27 turns. These are directions, not rates.
- "Industry" here means the course notes plus the cited papers; the notes quote ranges without naming the products they came from.
- Our turn-around figure starts at the real end of speech from the session record (`speech_end_to_first_audio_ms`) and includes the Pi playback path, which the industry figures usually do not.
- The server-side hedge comparison mixes warm-ups, scripted test requests and live turns; read it as a direction.

## Update 2026-10-27: where we stand after the keep-alive, pre-rendered replies, clock answers and warm-up

Pooled from the two spoken sessions of 2026-10-27 (`out/sessions/speaking-1`, `speaking-2`; normal-pause turns only; one speaker, one room; n=38). End of speech to first sound:

| Turn type | n | Median | p90 |
|---|---|---|---|
| All turns | 38 | **2.29 s** | 2.98 s (p95 3.14 s, max 4.74 s) |
| Commands and clock questions (no LLM) | 8 | **1.17 s** | 1.39 s |
| Plain LLM answers | 9 | 2.16 s | 2.58 s |
| Tool turns through the LLM | 21 | 2.63 s | 3.14 s |

Against the sources verified on 2026-10-26: real phone-call medians 1.30-1.74 s, p95 1.77-2.26 s (OpenBenchmarks); tool-workflow mean response 1.27 s (ElevenLabs), 1.58 (GPT Realtime), 1.82 (Telnyx), **1.97 (Pipecat)**, 2.21 (Retell), 2.46 (PolyAI), 2.59 (LiveKit), 3.08 (Vapi) (Cekura). So: **mid-pack for a tool-calling agent, about 0.6-1.0 s slower than the best phone medians, with a tail about 1 s worse (our p95 3.14 s against 1.8-2.3 s); our no-LLM commands (1.17 s) are at the best level.** The sources do not measure the same thing (carrier path, mean against median, unknown clock definition in Cekura, our one speaker and room), so this is direction.

**Why the hosted platforms are faster, and how much is infrastructure** (my estimate from the numbers above, not a measurement). Daily's budget for the best hosted stack is network 200 + turn detection and transcription 400 + LLM 500 + TTS 200 ms = 1.3 s. Ours: turn end plus transcript about 1.0 s (0.64 s hold + 0.37 s offline finalise), LLM first call about 0.9 s, TTS 0.35-0.5 s. Roughly a third to a half of the gap to the best phone medians is infrastructure we cannot change (LLM distance and capacity from Manila, GPU TTS and STT); the rest is our choices (the hold and offline finalise, a large tool prompt, the slow weather tool, the Pi playback path, and cold connections, now fixed). Two things cut the other way: their numbers include network that our local STT and TTS legs do not pay, and the hosted **Pipecat** figure (1.97 s), the same framework we use, is only about 0.3-0.6 s below ours. **Honest floor for this architecture (local STT and TTS on a laptop, cloud LLM): about 1.8-2.0 s**, against 1.3-1.5 s for the best hosted stacks. The levers that get there are the early decode (about 0.3 s on every turn), caching the weather lookups (1-3 s on weather turns) and a wider fast path.

## Update 2026-10-29: the local router (fine-tuned Granite 4.0 H-1B) next to Gemini only

**Bottom line.** The router does not change where we stand: still mid-pack for a tool-calling agent, about 0.6-1.0 s behind the best phone medians and 2-3x the "good" 600-900 ms target. The median is the same with it as without it (2.3-2.4 s, within the noise of 14-15 turns per run). Its value is resilience when the network is slow or down, and fewer Gemini requests on the turns it answers; the offline-network case has only been tested with a fake model, not live.

What it is: a LoRA fine-tune of `ibm-granite/granite-4.0-h-1b` (v1, template data, one seed), served as a Q4_K_M GGUF by llama-server on the laptop CPU, writing one grammar-constrained line per call (`finetune/`, `pipeline/src/voice_pipeline/router.py`). It runs home commands, the clock, weather, Wikipedia and the Pi volume itself and defers the rest to Gemini; it races Gemini and the first to answer wins (`--router-url`, off by default).

Spoken sessions of 2026-10-29 (the same 15 phrases each time, one speaker, one room, Pi loudspeaker at 80%, Gemini on IPv4; interrupted turns excluded). End of speech to first sound:

| Configuration | Session | n | Median | p90 | Max |
|---|---|---|---|---|---|
| Gemini only (router off) | `router-1029-0648` | 15 | **2.32 s** | 2.94 s | 3.30 s |
| Router + Gemini, plain race | `router-1029-0700` | 14 | **2.40 s** | 3.14 s | 3.16 s |
| Router + Gemini, Gemini held 1 s | `router-1029-0640` | 15 | 2.33 s | 3.12 s | 3.40 s |
| Reference, 2026-10-27 (Gemini only, free speech) | `speaking-1`, `-2` | 38 | 2.29 s | 2.98 s | 4.74 s |

The final transcript arrives 0.05-1.3 s after the end of speech, a spread that has nothing to do with the router, so the comparison below uses the part the router can change, final transcript to first sound, phrase by phrase (11 phrases heard in all three runs):

| Phrase | Gemini only | Hold 1 s | Plain race |
|---|---|---|---|
| "What time is it?" (fast path) | 400 ms | 400 | 374 |
| Brightness to forty percent | 1258 | 1217 (router) | 1201 (router) |
| Timer for ten minutes and lights off | 2063 | 1122 (router) | 1121 (router) |
| "Call my sister." | 1258 | 987 (router) | 988 (router) |
| "Call Gary." | 1355 | 1209 (router) | 1254 (router) |
| "And tomorrow?" (weather follow-up) | 1260 | 1346 (router) | 2142 (Gemini) |
| Speaker volume | 1587 | 1232 (router) | 1345 (Gemini) |
| "Who is Cormac McCarthy?" (garbled in two runs) | 1860 | 2027 (Gemini) | 1968 (Gemini) |
| "Thanks, that's all." | 1137 | 1140 (router) | 1084 (Gemini) |
| "Why do you think the sky is blue?" | 1139 | 1437 (Gemini) | 1322 (Gemini) |
| Document question | 1910 | 2246 (Gemini) | 2122 (Gemini) |
| **Median / mean** | **1260 / 1384** | **1217 / 1306** | **1254 / 1356** |

- **Where it helps:** turns that would cost Gemini a second request (timer plus lights, -0.94 s; Tokyo time and speaker volume earlier, -0.4 s) and some single commands (-0.3 s); single-call commands gain almost nothing, because Gemini's own short spoken filler ("I'm setting the brightness...") already arrives at about 0.8 s, when the router answers.
- **Where it costs:** turns the router defers pay for the hold (about +0.3 s with a 1 s hold: sky, document). The plain race has no hold, but Gemini's filler then often wins the turn and the router's complete answer is dropped (the router answered 4 turns instead of 8 with the hold), and a slow Gemini second half is not rescued ("And tomorrow?" 2142 ms).
- **Router call time:** 0.39 s p50 / 0.66 s p95 offline on the laptop CPU, but 0.6-1.0 s (median about 0.7) live, because the speech server shares the 4 cores.

Router quality (one run, one seed, greedy decoding; `docs/LOCAL-LLM-BASELINE-FEEDBACK.md`):

| | OnIt set | 92-turn pack | Defer recall / precision | False tool calls |
|---|---|---|---|---|
| Gemini (18 tools) | 57/63 (90.5%) | 77/92 (83.7%) | n/a (cannot defer) | 8 of 25 no-tool turns (3 are reasoning turns where only a deferral is accepted) |
| Router v1, Q4_K_M | 69/77 (89.6%); 58/63 on Gemini's subset | **80/92 (87.0%)** | 85.5% (OnIt 96.8%, pack 35.7%) / 100% | 2 of 25 |

Reading it against the industry rows above: the 2.3-2.4 s median sits between Retell (2.21 s) and PolyAI (2.46 s) in the tool-workflow means; only the fast-path turns (about 1.1 s) are inside the phone-call range of 1.30-1.74 s. About 1.0 s of every turn is the end-of-turn hold plus recogniser, which the router does not touch, so it cannot move the headline number; a faster first LLM call or a shorter hold could.

Caveats: 14-15 timed turns per run, one speaker, so differences under about 0.3 s are noise; the Gemini-only run and the router runs were separate sessions on the same script; turns the recogniser garbled differ between runs; the router has not been tested on a throttled or offline connection; the old-benchmark figures quoted from other days are from this document and `docs/LIVE-AGENT.md`, not recomputed.

## Update 2026-10-29 (later): the 350M router (granite-4.0-h-350m, KD) next to the H-1B router

**Bottom line.** Swapping the local router from the fine-tuned H-1B to the fine-tuned **granite-4.0-h-350m (`router-kd-s0`)** cuts the median end of speech to first sound from 2.3-2.4 s to **1.44 s** (one spoken session, 15 turns). That is inside the real phone-call range (medians 1.30-1.74 s) and below every tool-workflow mean in the Cekura list except ElevenLabs (1.27 s). The tail is unchanged on turns Gemini answers; the 1.44 s is bought by the router answering 9 of 15 turns.

Offline evaluation (Q4_K_M, llama.cpp CPU, 8 threads, one server at a time, greedy decoding, one run each; scorer and sets as in `docs/LOCAL-LLM-BASELINE-FEEDBACK.md`). Adapters `martinnavs/granite-4.0-h-350m-router-{v1,kd}-s0`, merged into `ibm-granite/granite-4.0-h-350m` and converted with the steps in that document (223 MB each). The only test sets in the repo are the OnIt set and the pack; there is no generated dev/test split in this checkout. False-call rate and defer recall/precision were not computed.

| Router | OnIt (77 turns) | Pack (92 turns) | Call p50 / p95, OnIt | Call p50 / p95, pack |
|---|---|---|---|---|
| H-1B v1 (was hosted) | 69/77 (89.6%) | 80/92 (87.0%) | 0.34 / 0.59 s | 0.40 / 0.63 s |
| 350M v1-s0 | 74/77 (96.1%) | 75/92 (81.5%) | 0.11 / 0.14 s | 0.12 / 0.17 s |
| **350M kd-s0 (now hosted)** | **76/77 (98.7%)** | 79/92 (85.9%) | 0.11 / 0.15 s | 0.12 / 0.17 s |

The H-1B rows reproduce the earlier run exactly (69/77, 80/92). The 350M kd-s0 pack score is 1 turn below H-1B and just above the 85% target.

Spoken session `router-1029-0939` (350M kd-s0, plain race, same 15 phrases, one speaker, one room, Pi loudspeaker; the interrupted phrase 15 excluded, so n=15 with an added "what model are you?" turn). End of speech to first sound, p90 computed the same way for all rows:

| Configuration | Session | n | Median | p90 | Max |
|---|---|---|---|---|---|
| Gemini only | `router-1029-0648` | 15 | 2.32 s | 2.70 s | 3.30 s |
| H-1B router + Gemini, plain race | `router-1029-0700` | 14 | 2.40 s | 2.89 s | 3.16 s |
| **350M kd-s0 + Gemini, plain race** | `router-1029-0939` | 15 | **1.44 s** | 1.94 s | 3.05 s |

- **Router call live:** 0.16-0.26 s (median 0.21 s), against 0.6-1.0 s for the H-1B live, which shared the 4 cores with the speech server.
- **Who answered:** the router won 9 of 15 turns (H-1B plain race: 4). Final transcript to first sound, median: 0.57 s against 1.26-1.29 s.
- **Per phrase, final transcript to first sound** (Gemini only / H-1B plain race / 350M): brightness 1258 / 1201 / 567 ms; timer plus lights 2063 / 1121 / 498; "Call my sister" 1258 / 988 / 421; "Call Gary" 1355 / 1254 / 415; "And tomorrow?" 1260 / 2142 / 540; sky 1139 / 1322 / 1471; document question 1910 / 2122 / 2061 (Gemini answered the last two, so they did not move).
- **Lines checked:** all 9 router answers were correct (`BRIGHTNESS|40 percent`, `TIMER|10 minutes` + `LIGHT_OFF`, `CALL|Anna` via the phonebook, `CALL|Gary`, `weather|Baguio|tomorrow`, `get_current_time|Asia/Tokyo`, `get_volume`, `wiki_summary|Cormac McCarthy`, `none|You're welcome.`). The document and notes questions were deferred. One doubtful deferral: "Sorry, what model are you?" gave `escalate|tools` where chat was right (costs about 1.1 s).

Against the industry rows above: median 1.44 s sits between the best phone medians (Telnyx 1.30, ElevenLabs 1.42) and Vapi (1.56); p90 1.94 s is inside the phone-call p95 range (1.77-2.26 s); still above the course notes' 0.6-0.9 s band, which describes plain answers with no tool. About 0.9 s of each turn is the end-of-turn hold plus recogniser, untouched by the router.

Caveats: 15 timed turns, one run, one speaker; differences under about 0.3 s are noise (the gain on router-answered turns, about 0.7 s, is well above that); the Gemini-only and router runs were separate sessions; not tested on a throttled or offline network; the hold variant (`--router-hold`) was not run with this model. The owner decided to keep the 350M kd-s0 as the default router for now (2026-10-29).

## Update 2026-10-29 (evening): Kitten TTS, the stress batch, and the numbers against the industry again

**Bottom line.** With the 350M router and **Kitten TTS Micro (voice Jasper)** in place of Supertonic, the median end of speech to first sound is **1.26-1.44 s**, inside the real phone-call medians (1.30-1.74 s) and below every tool-workflow mean except ElevenLabs (1.27 s). The TTS leg now meets its 250 ms target at the median. The tail (p90 2.3-2.8 s) and the course-note bands (good 0.6-0.9 s) are unchanged: about 0.9 s of every turn is the end-of-turn hold plus recogniser, and the turns Gemini answers are unchanged.

**Choosing the voice** (all in `~/work/tts-bakeoff`, outside the repo; 2 CPU threads; 39 real bot sentences):
- UTMOS (automatic naturalness score) put Supertonic F1 at 5 steps (4.35) level with Kitten Micro (4.26-4.29), and Supertonic F4/F5 below F1 at 4 steps (3.59, 3.66 against 3.97). Short-chunk time: Supertonic F1 311 ms, Kitten Micro Jasper 225 ms.
- A **blind A/B listening test** (30 trials, one listener, laptop speakers, loudness-matched) chose Kitten over Supertonic in **20 of 20** comparisons (Jasper 13/15, Luna 12/15 wins; Supertonic 4 steps 3/15, 5 steps 2/15). UTMOS did not predict this, and the 5th step was not audible. One listener, one session; sentences played alone, so chunk seams were not tested.
- Parakeet round trip (no hotwords / server name list): real replies word error 4.0% / 3.3% for Jasper against 3.7% / 3.5% for Supertonic; 32 target names right 81% / 88% against 78% / 84%. A tie. Both miss EDSA, Suttree and Anne Carson.
- Kitten's own text cleaner lowercased everything, dropped commas and turned "person's" into "person ess", "AM" into the word "am" and "1933" into "one thousand nine hundred thirty-three". `KittenTTSEngine` (`src/lan_speech/engines.py`) keeps case, apostrophes and punctuation, spells AM/PM as letters and says years as years. `kittentts` is not a project dependency: set `KITTEN_SITE` to a site-packages directory that holds it (`--tts-engine kitten --kitten-voice Jasper`).

**The router needs few threads.** The 350M model on 8 llama.cpp threads timed out on every request when 3 of the 4 cores were busy (3 s cap) and on every turn of one stress run, so Gemini answered everything (3-6 s). With 3 threads: 40 ms idle, 54 ms under the same load (4 threads: one 2.5 s stall; 2 threads: 65 ms). Run it with `-t 3 -tb 3`.

End of speech to first sound, the same method for every row (interrupted and unanswered turns excluded):

| Measure | Industry target | Gemini only (`0648`) | Router + Kitten, 15 phrases (`1127`) | Router + Kitten, stress batch (`1204`) |
|---|---|---|---|---|
| End of speech to first sound, median / p90 | phone medians 1.30-1.74 s (p95 1.77-2.26 s); tool-workflow means 1.27-3.08 s; course notes good 0.6-0.9 s, sluggish over 1.2 s | 2.32 / 2.70 s | **1.44 / 2.80 s** | **1.26 / 2.28 s** |
| End-of-turn hold | 200-500 ms | 638 ms | 642 ms | 638 ms |
| Transcript ready (hold + finalise) | streaming delay under 50 ms | 1019 ms | 929 ms | 908 ms |
| TTS first audio, median / p90 | under 250 ms | 364 / 504 ms | **199 / 305 ms** | **178 / 240 ms** |
| Cut to silence on a barge-in | under 50-100 ms | n/a | n/a | **0.2-0.9 ms** (4 barge-ins) |

`1204` is a different phrase mix (more home commands the router handles), so only `0648` against `1127` is like for like. In `1204` the router answered 10 of 21 timed turns (median 1.24 s, p90 1.37 s) and Gemini the other 11 (median 1.46 s, p90 2.32 s); the router's own call took 0.12-0.35 s; TTS had 0 underruns.

**Stress batch** (`.scratch/router-voice/stress-batch.md`, 20 turns, 4 interruptions; session `router-1029-1204`): 12 pass, 3 partial, 4 fail, 1 not run as written. The three barge-ins and the stop word all worked and the follow-up after each was handled. Found and fixed afterwards: (1) `ALARM|6:30 AM|tomorrow` put the whole string in the slot and the reply said "6:30 AM|tomorrow" (now folded into "6:30 AM tomorrow"); (2) a garbled turn ("Bucket on Mu.") was parroted back as the router's reply (now handed to Gemini when a 3-word-or-longer reply repeats the turn); (3) a failed read-only lookup is retried once when the failure looks transient (the retry was not exercised live). Not fixed: "I'm feeling cold" had a good router reply rejected by the "chat reply too long" rule and Gemini then set the thermostat to 24 degrees; the weather tool needed about 11 s on a first lookup of a new place (fillers played; the retry asked again and got 2.3 s); a hesitant utterance that was resumed ran the brightness command twice.

Caveats: 15 and 21 timed turns, one speaker, one room; differences under about 0.3 s are noise; separate sessions; pronunciation of AM/PM, years and apostrophes was not listened to after the text fix; not tested on a throttled or offline network.

## Update 2026-10-29 (final test): router, Kitten voice, Tavily search and echo guard together

**Bottom line.** In the final 44-turn stress run (`router-1029-1408`, `STRESS-TEST.md`) the median end of speech to first sound was **1.50 s**, inside the real phone-call medians (1.30-1.74 s). The turns the router answers take **1.27 s** median (p90 1.91 s), level with ElevenLabs' 1.27 s tool-workflow mean. The turns that go to Gemini take **2.90 s** (p90 3.70 s), and the web-lookup turns **2.95 s** (p90 3.70 s), which is where the Cekura tool-workflow means of 2.46-3.08 s (PolyAI, LiveKit, Vapi) sit. The tail (p90 3.28 s overall) is still above the phone-call p95 range (1.77-2.26 s), and by the course notes' bands (good 0.6-0.9 s, sluggish over 1.2 s) every figure here is "sluggish": about 0.9 s of every turn is the end-of-turn hold plus recogniser, which none of today's changes touch.

The mix matters. This run is 44 turns of a script with many web lookups, weather and interruptions, so it is not like for like with the 15-phrase script (`0648`, `1127`): the median moves with how many turns the router can take. The mix is also why Gemini-answered turns here (2.90 s) are slower than in the 15-phrase script (about 1.5-2.3 s): in this run most of them are web lookups, which add a search of about 1.1-1.7 s after Gemini decides to call it.

| Measure | Industry target | Gemini only (`0648`, 15 phrases) | Router + Kitten (`1127`, 15 phrases) | **Final stress run (`1408`, 44 turns)** |
|---|---|---|---|---|
| End of speech to first sound, median / p90 | phone medians 1.30-1.74 s (p95 1.77-2.26 s); tool-workflow means 1.27-3.08 s; course notes good 0.6-0.9 s, sluggish over 1.2 s | 2.32 / 2.70 s | 1.44 / 2.80 s | **1.50 / 3.28 s** (n=34) |
| ...router answered | n/a | n/a | n/a | **1.27 / 1.91 s** (n=20) |
| ...Gemini answered | n/a | 2.32 / 2.70 s | n/a | **2.90 / 3.70 s** (n=14) |
| ...Tavily web lookups | tool-workflow means 2.46-3.08 s (PolyAI, LiveKit, Vapi) | n/a | n/a | **2.95 / 3.70 s** (n=11) |
| End-of-turn hold | 200-500 ms | 638 ms | 642 ms | 637 ms |
| Transcript ready (hold + finalise) | streaming delay under 50 ms | 1019 ms | 929 ms | 921 ms |
| Speech-server finalise | n/a | 442 ms | 305 ms | 325 ms |
| TTS first audio, median / p90 | under 250 ms | 364 / 504 ms (over) | 199 / 305 ms | **207 / 273 ms** |
| Cut to silence on a barge-in | under 50-100 ms | n/a | n/a | **0.2-0.8 ms** (7 barge-ins, median 0.7 ms) |
| False interruptions by its own echo | zero | n/a | n/a | **0** (0 `ignored_echo`, 7 replies of 40-64 words played to the end) |

Where the time goes on a web lookup (Gemini path, from the agent log): transcript ready about 0.92 s, Gemini's first text 1.4-3.4 s (it picks the tool and writes a one-line `say`, which is spoken first), TTS first audio 0.2 s. The Tavily call itself is 1.1-1.7 s with the kept-alive connection (`SESSION-2026-10-29.md`) and overlaps the `say` being spoken; a cached repeat is 6 ms. Prefetching the search from the router's escalation (not built) would save about 1 s here.

**What changed against this morning, honestly.** The 1.44 s of the 15-phrase script and the 1.50 s here are the same to within the noise (15 and 34 turns, one speaker, separate sessions); the gain over the Gemini-only 2.32 s comes from the router answering the home commands and chat that Gemini used to take. The new things in this run did not move the headline: Tavily made general-knowledge turns about 0.6 s slower than Wikipedia would have been (about 1.2 s search against 0.63 s) and in exchange answers questions Wikipedia could not (current office-holders, recent events, "what is sinigang"). The echo gate and the level-matched voice held: no false interruptions across the run.

**Reading it against the industry rows above.** Best phone medians (Telnyx 1.30 s, ElevenLabs 1.42 s) are matched on the router turns and approached overall; the tool-workflow figures (Pipecat 1.97 s, Retell 2.21 s, PolyAI 2.46 s, LiveKit 2.59 s, Vapi 3.08 s) are the right yardstick for the web-lookup turns, where we are at 2.95 s. Interruption handling is far inside its target; TTS first audio now meets its 250 ms target at the median.

Caveats: 34 timed turns, one speaker, one room; differences under about 0.3 s are noise; the three sessions compared were separate runs and different scripts; the figures include the Pi playback path, which industry figures usually do not; Gemini's speed varied by 1 s between turns of the same kind; not tested on a throttled or offline network or with a second speaker; what the replies sounded like was not part of this measurement. Bugs the run found (a date question answered with the time, a garbled turn parroted back, a wrong-year web answer, the thermostat set on "I'm feeling cold") are in `STRESS-TEST.md`.
