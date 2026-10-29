# Stress batch v2 (33 turns, 3 interruptions, 2 silent-reply checks)

Final test of 2026-10-29. Run: `NOTE="stress v2" bash .scratch/router-voice/run.sh`, say the lines in order, wait for each reply unless it says INTERRUPT or SILENT. Score: `python3 .scratch/router-voice/score_stress.py` (expectations are in `stress-batch-v2.jsonl`).

**INTERRUPT** = talk over the reply about N s after it begins, with the words given. **SILENT** = say nothing at all until the reply has finished (it is the echo-gate check). **known issue** = failed in an earlier run; it is here to see whether it still does.


### A. Warm-up and fast path

| # | Say | Listen for |
|---|---|---|
| 1 | Good morning. | short, natural; no delay |
| 2 | What time is it in Manila? | AM/PM spoken as letters, not the word 'am' |
| 3 | What's today's date? | date read naturally |

### B. Home commands (router)

| # | Say | Listen for |
|---|---|---|
| 4 | Set an alarm for six thirty AM tomorrow. | no 'pipe', 'tomorrow' kept, AM as letters |
| 5 | Turn off the lights and play some music. | two actions, one confirmation |
| 6 | Make it a little louder. | resolves 'it' from the music |
| 7 | Set a timer. | asks how long instead of guessing |
| 8 | Ten minutes. | fills the timer slot from the last question |
| 9 | I'm feeling cold.<br>*(known issue)* | must not change the thermostat; a chat reply or a question |
| 10 | Message Anna that I'm running late. | apostrophe read naturally; the contract has no message body |

### C. Contacts and robustness

| # | Say | Listen for |
|---|---|---|
| 11 | Call Bob. | says Bob is not in the phonebook; places no call |
| 12 | What does the fox say?<br>*(known issue)* | a trivia answer; NOT a phonebook line (router hallucination) |
| 13 | Paki-on mo yung ilaw. | Taglish: LIGHT_ON or a polite clarification; note what happens |

### D. Tavily web answers

| # | Say | Listen for |
|---|---|---|
| 14 | What is Sinigang? | first web lookup; accurate, 2 sentences |
| 15 | Who is the current president of the Philippines? | current fact |
| 16 | When did the EDSA revolution happen? | EDSA pronounced; year as 'nineteen eighty-six' |
| 17 | Who won the 2022 World Cup, and what was the score? | two-part question |
| 18 | And who scored the most goals? | follow-up: the context (World Cup) must carry into the query |
| 19 | What is Sinigang? | exact repeat: should come from the answer cache and be quicker than the first time |
| 20 | Who won the 2087 Nobel Prize in physics? | a future event: must say it can't know, not invent a winner |

### E. Weather (flaky tool)

| # | Say | Listen for |
|---|---|---|
| 21 | What's the weather in Cebu? | first lookup of a new place; fillers if slow (the tool was flaky today) |
| 22 | And tomorrow? | follow-up keeps the place (Cebu) |
| 23 | What's the weather in Davao tomorrow, and should I bring an umbrella? | answers both parts |

### F. TTS text paths

| # | Say | Listen for |
|---|---|---|
| 24 | What's the time in Tokyo? | AM/PM as letters; zone named |
| 25 | When was Blood Meridian published, and who is Cormac McCarthy's publisher? | year as 'nineteen eighty-five'; possessive 'McCarthy's' not 'McCarthy ess' |
| 26 | Explain how a rainbow forms.<br>*(known issue)* | LISTEN for seams between sentences; a real explanation, not one sentence (the router truncated this before) |

### G. Interruptions

| # | Say | Listen for |
|---|---|---|
| 27 | Tell me a short story about a dog and a typhoon.<br>**INTERRUPT after ~3 s with:** "Actually, turn on the lights." | stops within ~1 ms, then LIGHT_ON |
| 28 | Explain how airplanes fly.<br>**INTERRUPT after ~2 s with:** "Wait, stop." | stop word silences it; nothing new is said |
| 29 | Who is the current president of the Philippines?<br>**INTERRUPT after ~2 s with:** "Never mind, what's the weather in Baguio?" | cut a web answer; the new question is the weather |
| 30 | Uh... [pause 1.5 seconds] ...set the brightness to thirty percent. | one BRIGHTNESS call only (a resumed split ran it twice before) |

### H. Echo gate: silent replies

| # | Say | Listen for |
|---|---|---|
| 31 | Tell me about the history of the Philippines.<br>**SILENT during the reply** | STAY COMPLETELY SILENT for the whole reply: it must not be interrupted by its own echo (echo gate, min bar 0.06) |
| 32 | Explain how the internet works.<br>**SILENT during the reply** | again silent: second false-trigger check on a different reply |
| 33 | Thanks, that's all. | short closing |
