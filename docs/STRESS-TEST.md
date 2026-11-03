# The stress test (v2) and its first full result, 2026-10-29

A 33-turn spoken script that exercises every path built so far, and a scorer that checks a recording against it. Written to flush out bugs, latency problems and regressions in one sitting (about 15-20 minutes). v1 (20 turns, `router-1029-1204`) is described in `INDUSTRY-COMPARISON.md`; v2 supersedes it.

## Files

| File | What |
|---|---|
| `docs/stress/stress-batch-v2.md` | the sheet to read from: 8 groups, what to say, what to listen for |
| `docs/stress/stress-batch-v2.jsonl` | the same turns with machine-checkable expectations (route, tools, reply regexes, time limits, interrupt spec, known issues) |
| `scripts/score_stress.py` | matches a recording to the script and prints PASS/FAIL per item, latency by route and the interruption statistics |

## How to run

1. Bring the stack up (`SESSION-2026-10-29.md`, "How to bring it up"), with a **fresh agent-server log** (restart the agent server and ask it nothing beforehand).
2. `NOTE="stress v2" bash .scratch/router-voice/run.sh` and read the sheet in order, one turn at a time. Wait for each reply except where the sheet says **INTERRUPT** (talk over it about N seconds in, with the words given) or **SILENT** (say nothing until the reply ends: the echo-gate check).
3. `python3 scripts/score_stress.py` (newest session and newest agent log), or pass `out/sessions/<session>` and an agent log.

## The script

| Group | Turns | Covers |
|---|---|---|
| A. Warm-up and fast path | 1-3 | chat, clock with AM/PM, date |
| B. Home commands | 4-10 | alarm slot, compound command, follow-up ("make it louder"), missing slot then fill, ambiguous "I'm feeling cold" (known issue), message with an apostrophe |
| C. Contacts and robustness | 11-13 | unknown contact, trivia that must not become a phonebook line (known issue), Taglish |
| D. Tavily web answers | 14-20 | a first lookup, a current fact, an acronym and a year, a two-part question, a follow-up that needs context, an exact repeat (cache), a future event |
| E. Weather | 21-23 | a new place (flaky tool), a follow-up, a two-part question |
| F. TTS text paths | 24-26 | AM/PM in another zone, a year and a possessive, a long explanation (known issue: one sentence) |
| G. Interruptions | 27-30 | cut a story with a command, a stop word, cut a web answer with a new question, a hesitant pause (one command only) |
| H. Echo gate | 31-33 | two **silent** long replies, then a closing |

Verdicts: `PASS`, `FAIL`, `PASS*` (a known issue that did not recur) and `FAIL*` (a known issue still present). `NOT RUN` means the line was never matched. Turns that match nothing (repeats, retries, recogniser mishearings) are listed as extras. The matching is fuzzy and in order; the scorer can be fooled by a retry, so read the extras and the per-turn log (`out/sessions/<session>/turns.jsonl` with `pipeline/out/agent_server/*.jsonl`) before believing a PASS.

## Result: session `router-1029-1408` (44 turns, 33 script items, 0 not run)

25 pass, 2 known issues apparently gone, 5 fail, 1 known issue still present. Two of the five fails are the test's own settings (item 17 missed its 3.6 s limit by 0.1 s; item 31's reply was shorter than the 40-word minimum, though it passed the silent-reply check), so three are real: items 3, 13 and 20.

| Result | Items |
|---|---|
| Passed (25) | 1, 2, 4-8, 10, 11, 14-16, 18, 19, 21-25, 27-30, 32, 33 |
| Fail (real) | **3** date question answered with the time; **13** Taglish line parroted back (out of scope: the item is now skipped); **20** future-year question answered with the 1987 laureates |
| Fail (test setting) | 17 (3.70 s against 3.6 s), 31 (27 words against 40) |
| Known issue, still present | **9** "I'm feeling cold" set the thermostat to 24 degrees |
| Known issue, gone by luck | 12 (the router sent a joke, which the length rule rejected), 26 (the long explanation was long this time) |

**Latency**, end of speech to first sound, every answered turn that was not interrupted (n=34; the same method as the other benchmarks):

| | n | Median / p90 | Max |
|---|---|---|---|
| All | 34 | 1.50 s / 3.28 s | 4.53 s |
| Router answered | 20 | 1.27 s / 1.91 s | 2.89 s |
| Gemini answered | 14 | 2.90 s / 3.70 s | 4.53 s |
| of which `web_answer` turns | 11 | 2.95 s / 3.70 s | 4.53 s |
| Non-web, router | 18 | 1.24 s / 1.43 s | 2.02 s |
| Non-web, Gemini | 5 | 2.22 s / 2.86 s | 4.51 s |

TTS first audio 207 ms median (p90 273 ms). End-of-turn hold 637 ms; transcript ready 921 ms after speech ends; speech-server finalise 325 ms. Seven barge-ins, all deliberate, cut to silence in 0.2-0.8 ms (median 0.7 ms); the stop word silenced the reply and the follow-ups were handled (lights on, the weather). **Echo gate: 0 false interrupts and 0 `ignored_echo` over the whole session**, including seven replies of 40-64 words played to the end (one was the second silent reply; the first silent reply was only 27 words, so it did not stress the gate for long).

**Underruns:** 5, all on turns where a filler line played or the answer was late (turns 16, 25, 40, 43); none on the other turns. Read as the gap between a filler and its answer, not a TTS speed problem (inference).

## Bugs the run found

Status on 2026-11-03 (fixes in the working tree, tested; not yet run again in a spoken session):

1. **Date asked, time answered.** Heard as "What's the day's date?", the router wrote `get_current_time` and the reply was "It's 2:08 PM." **Fixed:** the router's clock reply follows the question (`fast_path.clock_question`: time, date, or both). Live: "Today is Tuesday, November 3." in 0.12 s.
2. **Near-copy of a garbled turn** ("Pucket on Muyoung Hilo") came from the Taglish line (item 13). **Not a bug:** Tagalog and Taglish are outside the English-only requirement, so item 13 is marked `skip` in the script and the scorer does not count it. The parrot check stays in the scorer for English turns.
3. **Wrong year.** "Who won the 2087 Nobel Prize in Physics?" returned the 1987 laureates. **Fixed generally**, not for years only: `web_answer` checks the numbers and names in the question against its answer and sources and says what it could not confirm (see `MCP-SERVERS.md`). Live: "I couldn't confirm 2087 in what I found. The closest result says: ..." 
4. **Thermostat on a complaint.** The router's reply ("That sounds chilly. Want me to warm the room?", the trained one) was 9 words and the chat-reply limit was 8, so Gemini took the turn and set 24 degrees. **Fixed at both ends:** the limit is 10 (on the router's eval outputs the only 9-10 word reply is this one; the other long ones are an invented joke at 13 and a made-up capability speech at 18), and the agent prompt already says a feeling is not a command (`730a9e0`). Live: spoken locally in 0.25 s, no tool called.
5. **A follow-up that needs a decision** ("should I bring an umbrella?") got the forecast again. **Fixed by the form of the question** (`voice_pipeline/question.py`: should I / do I need / is it safe, worth, better ...), not its topic: the router hands such a turn to Gemini ("needs reasoning"), and a lookup tool's result goes back to the model instead of being spoken as it is. Live: Gemini answered "...you might want to bring an umbrella just in case." in 1.8 s.
6. **Garbled question, confident answer.** "What is Negang?" (Sinigang misheard) was answered about a Malaysian engineering company. **Open:** the answer check accepts it (the name is found, with spelling tolerance, in the company's page); only a recogniser confidence or a clarifying question would catch it.
7. **Double Tavily calls.** One was the router's lookup (the user's words), the other Gemini's reworded call. **Fixed:** wordings with the same content words share one search, and the second call joins the first (`MCP-SERVERS.md`). It also starts Gemini's lookup a second earlier.

Slow turns were Gemini's: first text at 2.4 s (the World Cup question), 3.3 s (Baguio City weather) and 3.4 s (how the internet works).

## Not covered by this test

Listening quality (AM/PM as letters, years, apostrophes, "EDSA", sentence seams): the log shows the text, not the sound. A throttled or offline network, a second speaker or room, the Pi microphone, and an induced Gemini stall (`kill -STOP` the agent server's python process, ask, `kill -CONT`, per HANDOFF.md).
