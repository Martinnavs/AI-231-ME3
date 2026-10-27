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
