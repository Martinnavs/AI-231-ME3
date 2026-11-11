# Spoken test v4 (59 turns, 3 interruptions, 2 silent-reply checks)

**HELD-OUT TEST MATERIAL: stays on this laptop. Never commit it, never put it where the cluster's training can read it.**

Retest of the retrained router (kd-web3 or later) with notices on. Run `.scratch/spoken-v3/README.md`, say the lines in order, wait for each reply unless it says INTERRUPT or SILENT. Score: `STRESS_SCRIPT=docs/stress/spoken-test-v4.jsonl uv run --offline python scripts/score_stress.py`; the scorer matches turns by wording, so you may repeat or reorder a line.

**FIRST: read the Pi speaker's level (item in group B, or ask "what volume is the speaker at") and write it down.** The last group changes the volume; the line before the closing one restores it.

**INTERRUPT** = talk over the reply about N s after it begins, with the words given. **SILENT** = say nothing until the reply has finished (echo-gate check). Group A lines come in pairs: the action, then an unrelated request that must not repeat or vary the action. Group D lines are plain comments after the lookup above them: a short acknowledgement, no new lookup, no repeated action.


### A. Topic switches: an action, then an unrelated request (12 pairs)

| # | Say | Listen for |
|---|---|---|
| 1 | Make it twenty-three degrees in here. | setup: the action itself |
| 2 | Call Marisol. | says Marisol is not in the phonebook; no call, no thermostat change |
| 3 | Wake me at seven fifteen tomorrow. | setup: the action itself |
| 4 | Put a timer on. | asks how long; no timer and no copy of 7:15 |
| 5 | Dim the lights to twenty percent. | setup: the action itself |
| 6 | What's the weather like in Dumaguete today? | a forecast for Dumaguete, brightness untouched |
| 7 | Turn the lights off. | setup: the action itself |
| 8 | What is seventy-two divided by six? | a spoken sum, 12 |
| 9 | Change the light colour to orange. | setup: the action itself |
| 10 | Message Teodoro that I'm running late. | says Teodoro is not in the phonebook; no message |
| 11 | Set a timer for five minutes. | setup: the action itself |
| 12 | Set an alarm. | asks for a time; does not invent one from the timer |
| 13 | Warm it up to twenty-six degrees. | setup: the action itself |
| 14 | Will it be windy in Tagaytay tomorrow? | a forecast for Tagaytay, thermostat untouched |
| 15 | Play some music. | setup: the action itself |
| 16 | What's nine times eleven? | a spoken sum, 99; music not restarted |
| 17 | Lights on please. | setup: the action itself |
| 18 | Call Carlo. | calls Carlo (he is in the phonebook) |
| 19 | Remind me to buy rice. | setup: the action itself |
| 20 | Start a countdown. | asks how long; no second reminder |
| 21 | Set brightness to sixty percent. | setup: the action itself |
| 22 | Is it hot in Zamboanga right now? | the weather in Zamboanga, brightness untouched |
| 23 | Pause the music. | setup: the action itself |
| 24 | How much is two hundred and fifty plus one hundred and thirty? | a spoken sum, 380 |

### D/E. Web and document lookups, each followed by a statement

| # | Say | Listen for |
|---|---|---|
| 25 | Who won the Nobel Prize in literature in 2016? | web answer: Bob Dylan |
| 26 | Huh, I didn't expect that. | a statement: a short acknowledgement, no new lookup |
| 27 | And who won the Peace Prize in that year? | follow-up: the year 2016 must carry into the query |
| 28 | That makes sense. | a statement |
| 29 | What's the tallest building in the world right now? | web answer: Burj Khalifa |
| 30 | Wow, that's tall. | a statement |
| 31 | How many people live in Cebu City? | web answer with a number |
| 32 | That's more than I thought. | a statement |
| 33 | Who is John Grady Cole? | from the documents (All the Pretty Horses) |
| 34 | Interesting, I should read that one. | a statement |
| 35 | What happens at the end of The Road? | from the documents; a real summary |
| 36 | That's a sad ending. | a statement |
| 37 | What is the price of one bitcoin today? | a number with units |
| 38 | And what is that in Philippine pesos? | follow-up: 'that' is the bitcoin price; a converted figure |

### F. Interruptions

| # | Say | Listen for |
|---|---|---|
| 39 | Tell me about how volcanoes erupt.<br>**INTERRUPT after ~3.0s with:** "Actually, set a timer for ten minutes." | stops within ~1 ms, then TIMER |
| 40 | Describe how a refrigerator works.<br>**INTERRUPT after ~2.0s with:** "Stop." | stop word silences it; nothing new is said |
| 41 | Who won the most recent Wimbledon men's final?<br>**INTERRUPT after ~2.5s with:** "Never mind, turn off the lights." | cut a web answer (or the 'looking it up' line); the new request is the lights |

### G. Echo gate: silent replies (both checks)

| # | Say | Listen for |
|---|---|---|
| 42 | Tell me about the history of the Philippines.<br>**SILENT during the reply** | STAY COMPLETELY SILENT for the whole reply: it must not be interrupted by its own echo (echo gate, min bar 0.06) |
| 43 | Explain how the internet works.<br>**SILENT during the reply** | again silent: second false-trigger check on a different reply |

### C. Delegate (sandbox, build, status, cancel)

| # | Say | Listen for |
|---|---|---|
| 44 | Run a directory listing in the sandbox and tell me what's in it. | a delegated sandbox command; a short 'started' reply |
| 45 | Build me a small script that prints the first twenty primes. | a delegated build |
| 46 | Is it done yet? | status of the latest job, no job named |
| 47 | What's the status of the primes script? | status by description, not a job id; must find the job |
| 48 | Start a background job that counts the lines in every file in the sandbox. | a second delegated job |
| 49 | Cancel that job. | cancels the line-count job just started |

### B. The Pi: speaker volume and device status (last, volume changes), restore, close

| # | Say | Listen for |
|---|---|---|
| 50 | What volume is the speaker at right now? | reads the Pi speaker's level: WRITE IT DOWN, the restore line at the end needs it |
| 51 | Is the speaker plugged in? | yes/no from device_status |
| 52 | Put the speaker at forty percent. | right after a status question: must SET the volume, not read the status |
| 53 | How warm is the Pi running? | Pi temperature from device_status, not a web answer |
| 54 | Change the speaker volume to fifty percent. | right after a status question: must SET the volume |
| 55 | Ping google dot com and tell me the latency. | no router tool for this: Gemini takes it (a network check), not device_status |
| 56 | How much memory is free on the Pi? | memory from device_status |
| 57 | Set the Pi's volume to sixty percent. | sets the volume (replies audibly quieter or louder) |
| 58 | Set the speaker volume to [your level] percent. | RESTORE: say the level you wrote down; replies back to normal |
| 59 | Thanks, that's all for now. | short closing |
