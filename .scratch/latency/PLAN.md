# Plan: latency that holds up in the demo

**Status:** draft for the owner's confirmation (2026-10-24). **Branch:** `agy/interrupts`. **Code:** agy from level-3 tickets (the owner's default; the assistant writes and tests the reference first). Background: `.scratch/latency-research/SUMMARY.md`.

**Goal (owner, 2026-10-24):** the LLM's response time spikes (0.7-7.7 s within three minutes in the spoken check), and the demo will see the same. Build workarounds so a slow turn sounds deliberate and the worst cases never reach the audience, not only lower averages.

## Where the time goes (measured 2026-10-24)

Spoken check (`.scratch/mcp-set/PLAN.md`, one session): end of speech to first sound 3.5-10.9 s. Per turn: end-of-turn wait + recogniser ~1.0-1.3 s; **the LLM's first response 0.7-7.7 s** (7.7 s for "Turn on the lights", before any tool); tool + **second LLM request** 1.2-5 s; TTS first audio ~0.4-0.7 s. Dry runs the same day: first spoken text median 1.9-2.2 s on tool turns, 0.8-1.1 s on plain turns. Every answer is a tool turn now, so each pays two LLM requests. Open-Meteo: ~270 ms away, a fresh TLS connection ~0.6 s, and the kept connection did not survive 18 s between two questions.

## Tasks

| # | Task | Audience effect | Files (planned) | Checked by |
|---|---|---|---|---|
| R1 | **Measure the spikes** (no product code): a probe sends a fixed prompt mix to the agent server once a minute for 30-60 min, each prompt **twice at once**, logging first-call / first-text for both. Answers: how often, how long, and whether a second request is usually fast when the first is stuck (decides R4). Also Open-Meteo's idle keep-alive limit | - | `scripts/agent_latency_probe.py` | under 1 US cent; a short report |
| R2 | **Never silent, never hung**: (a) a filler sentence when a tool call arrives, from about 7 rotating natural phrases per tool plus a default set, never the same twice in a row, one per turn, sent to the speaker and kept out of the LLM history; (b) a second "still working" phrase if no answer text has come about 3 s after the filler; (c) at about 8 s with no answer, cancel the turn and say so politely. Phrases and the two delays in a config file | at most ~1-2 s of silence on any turn; a stuck request ends with a sentence | `pipeline/src/voice_pipeline/agent.py`, `pipeline/config/fillers.json`, tests | unit tests with a fake clock; dry run unchanged; spoken check |
| R3 | **Fast path for common ME2 commands** (no LLM): a strict matcher for unambiguous phrasings (lights on/off, play/pause/stop/next music, volume up/down, a timer or alarm with a clear value, the speaker volume with a number) calls the tool directly and speaks a fixed confirmation; anything not matched exactly goes to the LLM as now; the turn is added to the history so follow-ups still work | the most likely demo commands answer in well under a second, LLM spikes or not | `pipeline/src/voice_pipeline/fast_path.py` (new), `agent_server.py`, tests | the 72-prompt dry run: matches must be right, and no false matches on the others |
| R4 | **Retry or hedge a stuck request**: if the LLM has produced nothing by a deadline, start a second request and take whichever streams first | a spike costs the deadline, not the spike | `agent.py` / `agent_server.py` | **only if R1 shows it wins**; otherwise dropped |
| R5 | **One LLM request instead of two** for tools that already return a sayable sentence (`weather`, `wiki_summary`, `get_volume`, `set_volume`): `FunctionCallResultProperties(run_llm=False)` plus the sentence added to the history. Gated on a feasibility test first (the research claims Gemini rejects the next turn without that synthetic reply). And **keep the home forecast warm** (fetch at start, refresh every 10 min) | those answers come a whole LLM request sooner; home weather instant | `agent_tools.py`, `web_info.py`, tests | the feasibility test; dry run incl. follow-ups ("and tomorrow?") |
| R6 | **Demo-day routine**: a warm-up script that touches every server and tool before the audience; a network check (laptop to Gemini, Open-Meteo, Wikipedia; laptop to Pi); a tiny keep-alive request every 30-60 s while waiting so connections stay warm | no cold first question | `scripts/demo_warmup.py`, a short checklist in `docs/` | run it before a spoken session |

**Order:** R1 first (cheap, decides R4). Then R2 and R3 (the demo safety net; independent of how Gemini behaves on the day; disjoint files, one wave). Then R5, R6, and R4 only if R1 says so.

**Later:** early decode with tail padding (~0.3 s), the TTS first-chunk ladder (ticket 25), starting the LLM before the turn is final.

**Non-goals:** changing the model (`gemini-3.1-flash-lite` stays), the echo guard, the speech server.

## Existing tickets that apply (`.scratch/project2-voice-agent/tickets/`, checked 2026-10-24)

- **12 item 4 (fillers), replaces R2(a)/(b):** already specified and confirmed: pre-recorded clips (no TTS at play time), three kinds: `stall` when no LLM text 700 ms after the end of the turn (any turn, so it also covers an LLM spike with no tool), `notice` at 2.5 s (one honest line), `delegate`/tool when a tool call arrives; rotation, at most one per kind per turn, none once the answer has started; the echo guard counts filler audio as played; a barge-in stops it; never claims a result. Add R2(c), the polite give-up at about 8 s, which 12 lacks. Build it in `doc_chat` (the agreed order: "12 ... built in doc_chat and measured live first"): `doc_chat` owns the speaker, TTS and guard; the agent server sends one new message when a tool is called.
- **12 item 3 (speculative start):** later (Pipecat cancels it on tool calls).
- **15 (bench rig, shrunk):** plays recorded questions into the laptop mic and measures "you stopped to first sound" without a human: makes R1 and the demo rehearsals repeatable.
- **25 (laddered TTS):** small gain (0.15-0.2 s) on the first real answer sound.
- **27 (end-of-turn hold):** the 0.5-0.65 s hold; needs the owner's recordings; later.
- **10 (`delegate`):** not now (no slow tool). **20 (assistant server):** time and weather superseded by the MCP set; timers/alarms "spoken when due" remain (not latency). **26:** recognition, not latency. **14:** reuse its report ("first answer audio" separate from fillers).

## What the research adds (`.scratch/latency-research/SUMMARY.md`, run D)

- Timing: verbal fillers should start early (roughly 200-500 ms after the user stops, Lala et al. 2019); keep ticket 12's `stall` at 700 ms or bring it to about 500 ms; over 4 s is where the experience degrades (Maslych et al. 2025), so the honest `notice` at 2.5 s comes before that.
- Use speech, not only sounds: a chime confirms reception but does not make a long wait feel shorter.
- Rotation and skipping: rotate, never repeat back to back, and only speak a filler when the answer is actually late (it then skips itself on fast turns).
- Optional, cheap: Vapi's `bot_say` idea, a `say` argument in our tools so the model writes a context-aware first sentence ("Turning on the kitchen lights") inside the same tool call; spoken the moment the call arrives, with the canned clips as the fallback.
- Give-up: products stop at about 8 s (Alexa's limit) and apologise with an offer to retry: R2(c) at 8 s matches.

## Decisions needed at confirmation

1. R2: fillers kept out of the LLM history (recommended)? Delays about 3 s for "still working" and about 8 s for the polite timeout?
2. R2 and R3: the assistant writes the phrase sets and the fast-path phrase list for your review before ticketing?
3. R3: which commands may skip the LLM (proposal: the list in the table)?
4. R1: 30-60 minutes, two requests a minute (well under 1 cent)?
