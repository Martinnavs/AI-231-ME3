# MCP servers

Stdio MCP servers (FastMCP, `mcp>=1.2,<2`) that expose the agent's capabilities to subagents as tools. Each runs as `python -m voice_agent.mcp_servers.<name>`. Servers never write to stdout except MCP protocol; logging goes to stderr.

> Status: this design still needs the instructor's sign-off (DESIGN.md human checkpoint 1).

## Path confinement (shared by every server)

`voice_agent.mcp_servers._paths.resolve_inside(root, rel, *, must_exist)` is the only way a server turns a tool-supplied path into a filesystem path.

- `rel` must be a relative string. Rejected with `PathEscapeError`: empty, NUL byte, absolute (POSIX or Windows anchor), any `..` component.
- The joined path is resolved with symlinks; the result must be `root` or inside it (`is_relative_to` on resolved paths, so `media2` is not mistaken for `media`).
- `must_exist=True` and missing → `FileNotFoundError`.
- Servers map `PathEscapeError` to the tool error `"path outside media root"`; nothing is read or written.

## speech

Run: `python -m voice_agent.mcp_servers.speech [--stt-backend NAME] [--tts-backend NAME] [--media-root DIR] [--max-audio-s 120]`

| Flag | Default |
|---|---|
| `--stt-backend` / `--tts-backend` | `agent.stt_backend` / `agent.tts_backend` from `config/agent.toml`; `fake` in tests |
| `--media-root` | `paths.media_root` |
| `--max-audio-s` | 120 |

Backends are created on first use through the `audio_engine` registry (see `AUDIO-ENGINE.md`); a backend `SpeechBackendError` becomes a tool error and the server keeps running.

### Tools

| Tool | Returns | Errors |
|---|---|---|
| `transcribe_audio_file(path)` | `{"text", "language", "duration_s"}` | outside media root; missing or non-audio file (names the relative path); audio longer than `--max-audio-s` |
| `synthesize_speech_to_file(text, path, voice=None, overwrite=False)` | `{"path", "duration_s", "sample_rate"}` | outside media root; text over 2 000 chars; output exists without `overwrite=true`; output is a directory |
| `get_tts_voices()` | `[{"id", "name", "language"}]` | backend error |

`synthesize_speech_to_file` creates missing parent directories, only inside the media root.

### Resource

`speech://voices`: markdown list of the same voices as `get_tts_voices`.

## home

Run: `python -m voice_agent.mcp_servers.home [--base-url URL] [--state-dir DIR]`

| Flag | Default |
|---|---|
| `--base-url` | `home_dashboard_url` from `config/agent.toml` (default `http://127.0.0.1:8000`) |
| `--state-dir` | `paths.state_dir` from `config/agent.toml` (default `~/.local/state/voice_agent`) |

Wraps the ME2 dashboard's HTTP API as an IoT target. No auth layer is used as the dashboard is assumed to run on a trusted local network.

Scope note: base tasks work for any phrasing and value, but the dashboard accepts only closed vocabularies. This server covers only devices where fixed vocabularies are natural limits: **lights**, **thermostat**, and **phone**. Music is handled by the media server (ticket 19), while timers, alarms, and reminders are handled by the assistant server (ticket 20). The dashboard's own music, timer, alarm, reminder, weather, and time widgets are not driven by the agent. Real telephony and email tools are out of scope.

### Route Mapping & Tools

| Tool | Action / Params | HTTP Method | Endpoint | Body |
|---|---|---|---|---|
| `get_home_state()` | — | `GET` | `/api/state` | None |
| `control_lights(action, ...)` | `action="on"` | `POST` | `/api/light/power_on` | None |
| | `action="off"` | `POST` | `/api/light/power_off` | None |
| | `action="brighter"` | `POST` | `/api/light/increase` | None |
| | `action="dimmer"` | `POST` | `/api/light/decrease` | None |
| | `action="set_color", color=...` | `POST` | `/api/light/set_color` | `{"color": color}` |
| | `action="set_brightness", level=...` | `POST` | `/api/light/set_brightness` | `{"level": level}` |
| `set_thermostat(degrees)` | `degrees=...` | `POST` | `/api/thermostat` | `{"degrees": degrees}` |
| `phone(action, contact=...)` | `action="call", contact=...` | `POST` | `/api/phone/call` | None |
| | `action="hang_up"` | `POST` | `/api/phone/hang_up` | None |
| `send_message(contact, text)` | `contact=..., text=...` | `POST` | `/api/phone/message` | None |

Every action tool returns the dashboard's own `_respond` shape: `{"changed": bool, "message": str, "state": {...}}`.

### Phone Simulation & Logging

The ME2 dashboard simulates phone calls and messages without a real contact book. The `phone("call")` and `send_message` tools take a contact name and message text, post to the dashboard routes to trigger the visible UI update, and append a record to `paths.state_dir/phone.jsonl`:
`{"t": float, "action": "call" | "send_message", "contact": str, "text": str}`.
If `phone.jsonl` is not writable, the dashboard action still succeeds and the result carries `logged: false`.

### Validation & Errors

Arguments are validated against `/api/vocab` (fetched once at startup, refreshed on any 4xx response). Out-of-vocabulary values are rejected immediately with a list of allowed values, making zero HTTP calls.
- If the dashboard is unreachable at startup, the server starts anyway with an empty vocabulary; subsequent tool calls return `"home dashboard unreachable at <url>"` until the dashboard is reachable.
- Request timeouts (> 2 s) return `"dashboard timed out"`.
- If `phone("call")` lacks a contact or `send_message` lacks text, the server raises `"who should I call?"` or `"what should the message say?"` without making an HTTP call.

### Resource

`home://vocab`: JSON string of allowed vocabularies fetched from the dashboard (`GET /api/vocab`).

