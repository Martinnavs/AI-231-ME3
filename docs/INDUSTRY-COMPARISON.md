# Our numbers against the industry numbers (2026-10-25)

One place for "how do we compare", so nobody researches it again. **The industry targets are not new research**: they come from the owner's course notes (`docs/VOICE-AGENT-EVALUATION-METRICS.md`, untracked, read-only, section numbers below) and the latency research already in the repo (`.scratch/latency-research/SUMMARY.md`, `docs/LATENCY.md`). Our numbers come from three live spoken sessions and the agent server's log.

## Bottom line

**We are 2-3 times slower than the industry's "sluggish" line for end of speech to first sound, and the cause is the cloud LLM's first response plus the end-of-turn wait, not the speech models.** Median 2.58 s (p90 3.03 s) against "exceptional < 0.6 s, good 0.6-0.9 s, sluggish > 1.2 s". Where we beat or meet the targets: cutting the bot off when interrupted (1 ms against < 50-100 ms) and the fast path (1.65-1.81 s with no LLM). Where we are over: end-of-turn wait, LLM first token, TTS first audio.

A realistic target for a tool-calling Gemini agent on this setup is **about 1.5-2 s**, not 0.9 s: the model's first response alone (1.0-1.3 s) already exceeds a 0.9 s total budget. The industry's 0.6-0.9 s figures describe pipelines whose LLM answers in text without a tool round trip.

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

## Coverage: what has been looked up, and what has not

Covered (do not repeat): the targets in the table, filler timing research (Lala 2019, Maslych 2025), Stivers 2009 turn gaps, product behaviour for slow tools (Alexa, Vapi, Retell, Siri), the LLM choice and its first-token numbers, end-of-turn defaults, and the echo problem (`docs/ECHO-RESEARCH.md`).

**Not covered:** published latency figures for *tool-calling* voice agents specifically (the course notes describe text-only turns); speech-to-speech models other than Gemini Live (dropped for echo sensitivity and cost, `SUMMARY.md`); any measurement with a second speaker, another room or another loudspeaker distance.

## Caveats

- One owner, one room, one Pi loudspeaker; 27 turns. These are directions, not rates.
- "Industry" here means the course notes plus the cited papers; the notes quote ranges without naming the products they came from.
- Our turn-around figure starts at the real end of speech from the session record (`speech_end_to_first_audio_ms`) and includes the Pi playback path, which the industry figures usually do not.
- The server-side hedge comparison mixes warm-ups, scripted test requests and live turns; read it as a direction.
