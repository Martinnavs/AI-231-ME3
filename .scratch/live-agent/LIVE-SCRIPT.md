# Live speaking test, 2026-10-23: the agent (smart_home + docs MCP) in doc_chat

Setup: laptop mic, Pi speaker at 80%, echo guard flags as in HANDOFF section 0, `doc_chat --llm agent --no-docs --stream`, agent server log in `pipeline/out/agent_server/`. Phonebook: Anna (sister), Ben (brother), Mom, Dr. Reyes (dentist), Carlo (friend).

## Session 1: quiet (no beeps)

Read each line after the assistant has finished speaking.

| # | Say | Expected |
|---|---|---|
| 1 | Who was Cormac McCarthy? | search_docs |
| 2 | Turn on the lights. | LIGHT_ON |
| 3 | When did he win the Pulitzer Prize? | search_docs |
| 4 | Make the lights purple. | COLOR purple |
| 5 | What is The Road about? | search_docs |
| 6 | Set the temperature to twenty-four degrees. | TEMPERATURE 24 degrees |
| 7 | Play some music. | PLAY_MUSIC |
| 8 | Turn the volume down a little. | VOLUME_DOWN |
| 9 | Which of his books did the Coen brothers make into a film? | search_docs |
| 10 | Set a timer for forty-five minutes. | TIMER 45 minutes |
| 11 | Wake me up at six thirty tomorrow. | ALARM 6:30 AM |
| 12 | Remind me to call the dentist. | CREATE_REMINDER call the dentist |
| 13 | What are my reminders? | LIST_REMINDERS |
| 14 | Turn off the lights and pause the music. | LIGHT_OFF + PAUSE |
| 15 | Call my sister. | CALL Anna |
| 16 | Send a message to Anna saying I'm running late. | MESSAGE Anna |
| 17 | Set an alarm. | asks what time (no call) |
| 18 | Seven fifteen. | ALARM 7:15 (AM or PM) |
| 19 | What time is it? | TIME |
| 20 | Thanks, that's all for now. | no tool |

## Session 2: interruptions (a beep about 2.5 s into every reply)

Ask question 1. Each time you hear a beep, say the next interrupt line at once. When the assistant has finished, ask the next question. If you run out of lines, say "stop" at each further beep.

| Questions, in order | Interrupt lines, in order (one per beep) |
|---|---|
| 1. Tell me about Blood Meridian. | A. Stop. |
| 2. Tell me about Suttree. | B. Wait, stop. Turn on the lights instead. |
| 3. Tell me about All the Pretty Horses. | C. Hold on, what year was it published? |
| 4. Tell me about No Country for Old Men. | D. Stop, set a timer for ten minutes. |
| 5. Tell me about The Road. | E. Wait. |
| 6. Tell me about The Passenger. | F. Stop, play some music. |
| 7. Tell me about his early life. | G. Sorry, stop. What's the weather today? |
| 8. Tell me about Outer Dark. | H. Stop. |
| | I. Wait, make the lights warm white. |
| | J. Hold on, when did he die? |
| | K. Stop. |
| | L. Wait, pause the music. |
