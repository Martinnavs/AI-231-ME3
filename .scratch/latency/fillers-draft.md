# Draft for review: filler phrases and fast-path phrasings (R2, R3)

**Status:** reviewed by the owner (2026-10-24); final unless changed. Rules (ticket 12 + research run D): fillers never claim a result or say an action happened; one per kind per turn; never the same phrase twice in a row (least recently used first); none once the answer has started; pre-recorded clips, so playing one costs nothing. Edit freely: add, cut or reword.

## 1. Stall: no answer text 500 ms after the end of the turn (any turn)

Short, non-committal, said as the start of a reply.

1. "Mm, one moment."
2. "Okay, let me see."
3. "Hmm, okay."
4. "Right, just a moment."
5. "Sure, give me a moment."
6. "Let me think."
7. "Got it, one moment."

## 2. Tool fillers: when a tool call arrives and nothing has been said yet

Per tool; a default set for tools without their own. Used instead of a stall if the tool call comes first; at most one filler of these two kinds per turn. If the model wrote its own `say` sentence into the call (the `bot_say` idea), that is spoken instead.

**smart_home** (an action, not yet confirmed)
1. "On it."
2. "Okay, doing that now."
3. "Sure, one moment."
4. "Alright, give me a moment."
5. "Okay, executing your request."
6. "Right away."
7. "Sure thing."

**get_current_time / convert_time**
1. "Currently checking the time."
2. "Checking the clock."
3. "Let me look up the time."
4. "Okay, checking the time."
5. "Sure, let me see the time."
6. "Just a moment."
7. "Let me work that out."

**weather**
1. "Let me check the weather."
2. "One moment, looking at the forecast."
3. "Let me see what the forecast says."
4. "Checking the weather now."
5. "Okay, pulling up the forecast."
6. "Getting the weather now."
7. "Sure, checking the forecast."

**wiki_summary / search_docs**
1. "Let me look that up."
2. "One moment, checking."
3. "Let me see what I can find."
4. "Okay, looking into it."
5. "Good question, let me check."
6. "Let me pull that up."
7. "Just a second, searching."

**set_volume** (the Pi, a change)
1. "Sure, adjusting it."
2. "Let me adjust it."
3. "Okay, will adjust it."
4. "One moment, changing the volume."
5. "Sure, setting the volume."
6. "Okay, one second."
7. "Just a moment."

**get_volume / device_status** (the Pi, a reading)
1. "Let me check the speaker."
2. "One moment, asking the Pi."
3. "Okay, checking the speaker."
4. "Let me check on the Pi."
5. "One second, reading that."
6. "Just a moment."
7. "Okay, checking."

**default** (any other tool)
1. "One moment."
2. "Let me check."
3. "Okay, just a second."
4. "Let me see."
5. "Sure, give me a moment."
6. "Looking into it."
7. "Okay, one sec."

## 3. Notice: still no answer at 2.5 s (once, honest)

1. "Sorry, this is taking a little longer than usual."
2. "Still working on it, the connection's a bit slow."
3. "Bear with me, it's slow on my end right now."
4. "Almost there, sorry for the wait."
5. "Thanks for waiting, I'm still on it."
6. "Sorry about the delay, nearly there."
7. "This one's taking a moment, hang on."

## 4. Give-up: no answer at 8 s (turn cancelled)

1. "Sorry, that's taking too long. Could you ask me again?"
2. "I'm having trouble getting an answer right now. Can you try again in a moment?"
3. "Sorry, I lost my connection for a second. Could you say that again?"
4. "Sorry, I couldn't get that done just now. Want to try once more?"
5. "That didn't come through, sorry. Could you ask again?"

## 5. Fast path (no LLM): exact phrasings, then the real tool, then a fixed confirmation

A turn takes the fast path only if the whole transcript (lower-cased, punctuation removed, a leading "please", "hey" or stop word stripped, a trailing "please" or "thanks" stripped) matches one pattern; anything else goes to the LLM. The confirmation is spoken **after the tool answers**; on a tool error the tool's own message is spoken. The turn and the confirmation go into the history so follow-ups work.

| Command | Patterns (`{n}` a number, `{unit}` minutes/hours/seconds, `{time}` like "6:30 am" or "seven pm") | Tool call | Confirmation |
|---|---|---|---|
| Lights on | "turn on the lights", "turn the lights on", "lights on", "switch on the lights" | `smart_home(LIGHT_ON)` | "The lights are on." |
| Lights off | "turn off the lights", "turn the lights off", "lights off", "switch off the lights", "kill the lights" | `smart_home(LIGHT_OFF)` | "The lights are off." |
| Play music | "play music", "play some music", "play the music" | `smart_home(PLAY_MUSIC)` | "Playing music." |
| Pause | "pause", "pause the music", "pause music" | `smart_home(PAUSE)` | "Paused." |
| Stop music | "stop the music", "stop music" | `smart_home(STOP)` | "Music stopped." |
| Next | "next", "next song", "skip", "skip this song", "next track" | `smart_home(NEXT)` | "Skipping to the next one." |
| Volume up | "volume up", "turn it up", "turn the volume up", "louder" | `smart_home(VOLUME_UP)` | "Turning it up." |
| Volume down | "volume down", "turn it down", "turn the volume down", "quieter", "lower the volume" | `smart_home(VOLUME_DOWN)` | "Turning it down." |
| Timer | "set a timer for {n} {unit}", "timer for {n} {unit}", "{n} {unit} timer" | `smart_home(TIMER, "{n} {unit}")` | "Timer set for {n} {unit}." |
| Alarm | "set an alarm for {time}", "wake me up at {time}", "alarm for {time}" | `smart_home(ALARM, "{time}")` | "Alarm set for {time}." |
| Speaker volume | "set the speaker volume to {n} percent", "set the volume to {n} percent", "speaker volume {n}" | `set_volume({n})` | the tool's own sentence ("The speaker volume is now {n} percent.") |

Spoken numbers ("ten", "forty five") are converted with the same rule the LLM prompt uses. Not on the fast path: anything with "and", a question, a colour, brightness, temperature, a contact, a reminder, or any phrasing not in the table.
