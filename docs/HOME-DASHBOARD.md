# The home dashboard

**BLUF.** A page that shows what the voice assistant did to the home: lights, thermostat, timer, alarm, phone, reminders and the last command heard. It runs on the Pi (`make up` starts it), you open it on the laptop, and the agent's `smart_home` tool posts to it. Standard library only. Music is not here: Spotify plays it.

```
agent (Pi) --smart_home--> POST http://PI:8800/api/command  {"intent": "COLOR", "slots": {"COLOR": "purple"}}
laptop browser ----------> http://PI:8800/        (polls GET /api/state every second)
```

| Open it | |
|---|---|
| With the stack | `make up` prints `home dashboard: http://<Pi cable address>:8800`; open it in a browser on the laptop |
| Alone, no Pi | `uv run python -m home_dashboard --port 8000` then open <http://127.0.0.1:8000>; `--phonebook` picks the contacts file |
| Without the voice | the page has a "Send a command" form, or `curl -X POST localhost:8000/api/command -d '{"intent":"LIGHT_ON"}'` |

## Values are open

| Intent (slot) | Accepts | Rejected with a message the model hears |
|---|---|---|
| `BRIGHTNESS` (`PERCENT`) | "40 percent", "forty", "40%" | not 0 to 100 |
| `COLOR` (`COLOR`) | any colour word or `#hex` (about 45 known names; an unknown word shows grey) | never |
| `TEMPERATURE` (`DEGREES`) | "24", "twenty four degrees" | outside 10 to 35 |
| `TIMER` (`DURATION`) | "45 minutes", "1 hour 30 minutes", "an hour", "half an hour", "5:00" | under 1 s or over 24 h; a new timer replaces the running one |
| `ALARM` (`ALARM_TIME`) | "6:30 AM", "7pm", "18:45", "noon"; rings for 60 s | not a clock time |
| `CALL`, `MESSAGE` (`CONTACT`) | a name in `pipeline/config/phonebook.json` | a name that is not there |
| `CREATE_REMINDER` (`TASK`) | any text; the same text twice within a minute is one | |
| `LIST_REMINDERS`, `LIGHT_ON`, `LIGHT_OFF` | no slot | |
| music intents | accepted and ignored ("music is played by Spotify") | |

A malformed command is a 400 with `{"ok": false, "error": "..."}`; the agent reads the error and says so. Reminders are also kept (and read aloud, newest three first) by the harness itself, so the spoken list does not depend on the page.

## Files

`src/home_dashboard/`: `parse.py` (open values to numbers), `state.py` (the 17 commands and the clock-driven state: no background thread), `server.py` (HTTP), `static/` (the page). Tests: `tests/home_dashboard/`. Wiring: `scripts/stack/pi_service.sh` (starts it beside the apps server and passes `--live` to the agent server), `PORT_DASH` (8800) in `scripts/stack/lib.sh`.

It replaces the closed-value ME2 panel (3 colours, 3 brightness levels, 3 temperatures, 3 reminders): that page's idea, rebuilt for open values and for this harness.
