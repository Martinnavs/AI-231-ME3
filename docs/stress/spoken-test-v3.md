# Spoken test v3 (39 turns, 3 interruptions, 2 silent-reply checks)

First spoken test of the router with `web_answer`, the call policy and the documents-vs-web race (2026-11-04). Run `.scratch/spoken-v3/README.md`, say the lines in order, wait for each reply unless it says INTERRUPT or SILENT. Score: `STRESS_SCRIPT=docs/stress/spoken-test-v3.jsonl python3 scripts/score_stress.py`.

**INTERRUPT** = talk over the reply about N s after it begins, with the words given. **SILENT** = say nothing until the reply has finished (echo-gate check). **known issue** = failed in an earlier run. Group J changes the Pi speaker's volume: write down the level it reads out, and restore it at the end.


### A. Warm-up (fast path)

| # | Say | Listen for |
|---|---|---|
| 1 | Good morning. | short, natural; no delay |
| 2 | What time is it in Manila? | AM/PM spoken as letters, not the word 'am' |

### B. Home commands and contacts (router, laptop smart-home tool)

| # | Say | Listen for |
|---|---|---|
| 3 | Turn on the kitchen lights. | router, one short confirmation |
| 4 | Set an alarm for six thirty AM tomorrow. | no 'pipe', 'tomorrow' kept, AM as letters |
| 5 | Set a timer. | asks how long instead of guessing |
| 6 | Ten minutes. | fills the timer slot from the last question |
| 7 | I'm feeling cold.<br>*(known issue)* | must not change the thermostat; a chat reply or a question |
| 8 | Call Gary. | says Gary/Bob is not in the phonebook; places no call |

### C. Sums: must never become a timer (ticket 02)

| # | Say | Listen for |
|---|---|---|
| 9 | What's fourteen times twelve? | a spoken sum, 168; NOT a timer |
| 10 | How much is three hundred divided by four? | 75; read naturally |

### D. Web answers: router -> web_answer (grounded Gemini)

| # | Say | Listen for |
|---|---|---|
| 11 | Who is the current Secretary General of NATO? | first web lookup: the 'looking it up' line may play at ~1 s; accurate, 1-2 sentences |
| 12 | What is the price of gold per ounce right now? | a number with units; no 'as of my training' |
| 13 | Who won the 2022 World Cup, and what was the score? | two-part question |
| 14 | And who scored the most goals? | follow-up: the context (World Cup) must carry into the query |
| 15 | Who painted the Mona Lisa? | a settled fact: fast (the model should not search) |
| 16 | Who won the 2087 Nobel Prize in physics? | a future event: must say it can't know, not invent a winner |
| 17 | When did the EDSA revolution happen? | EDSA pronounced; year as 'nineteen eighty-six' |

### E. Your documents: the documents-vs-web race

| # | Say | Listen for |
|---|---|---|
| 18 | When was No Country for Old Men published? | answered from the documents: faster than a web answer |
| 19 | Who is Lester Ballard? | from the documents (Child of God) |
| 20 | What was Cormac McCarthy's first novel? | The Orchard Keeper; possessive 'McCarthy's' not 'McCarthy ess' |
| 21 | Where is Suttree set? | Gemini + document search: Knoxville |
| 22 | When did Cormac McCarthy die? | from the documents; year read as 'twenty twenty-three' |
| 23 | How tall is Mount Everest? | NOT from the documents (control): the documents must stay out of it |

### F. Weather

| # | Say | Listen for |
|---|---|---|
| 24 | What's the weather in Cebu tomorrow? | first lookup of a new place; a forecast |
| 25 | Should I bring an umbrella in Davao tomorrow? | calls weather, then answers yes/no (a decision: Gemini) |

### G. TTS text paths

| # | Say | Listen for |
|---|---|---|
| 26 | What's the time in Tokyo? | AM/PM as letters; zone named |
| 27 | Explain how a rainbow forms.<br>*(known issue)* | LISTEN for seams between sentences; a real explanation, not one sentence (the router truncated this before) |

### H. Interruptions

| # | Say | Listen for |
|---|---|---|
| 28 | Tell me a short story about a dog and a typhoon.<br>**INTERRUPT after ~3s with:** "Actually, turn on the lights." | stops within ~1 ms, then LIGHT_ON |
| 29 | Explain how airplanes fly.<br>**INTERRUPT after ~2s with:** "Wait, stop." | stop word silences it; nothing new is said |
| 30 | Who won the most recent Ballon d'Or?<br>**INTERRUPT after ~2.5s with:** "Never mind, what's the weather in Baguio?" | cut a web answer (or the 'looking it up' line); the new question is the weather |
| 31 | Uh... [pause 1.5 seconds] ...set the brightness to thirty percent. | one BRIGHTNESS call only (a resumed split ran it twice before) |

### I. Echo gate: silent replies

| # | Say | Listen for |
|---|---|---|
| 32 | Tell me about the history of the Philippines.<br>**SILENT during the reply** | STAY COMPLETELY SILENT for the whole reply: it must not be interrupted by its own echo (echo gate, min bar 0.06) |
| 33 | Explain how the internet works.<br>**SILENT during the reply** | again silent: second false-trigger check on a different reply |

### J. The Pi: speaker volume and device status (last, volume changes), then close

| # | Say | Listen for |
|---|---|---|
| 34 | How loud is the speaker? | reads the Pi speaker's level: WRITE IT DOWN (item below restores it) |
| 35 | What's the Pi temperature? | the Pi's temperature from device_status, NOT a web answer (ticket 02 fixed this wording) |
| 36 | Is the speaker connected? | yes/no from device_status |
| 37 | Set the speaker to thirty percent. | the next replies are audibly quieter |
| 38 | Set the speaker to eighty percent. | RESTORE: say the level you wrote down if it was not 80; replies back to normal |
| 39 | Thanks, that's all. | short closing |
