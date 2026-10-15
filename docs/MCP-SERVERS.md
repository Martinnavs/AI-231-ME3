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

## code

Run: `python -m voice_agent.mcp_servers.code [--root DIR]` (`--root` defaults to `paths.sandbox_root`; created if missing). Site URLs use `sites_host` and `sites_port` from `config/agent.toml`.

Tool paths go through `resolve_inside` against the sandbox root; an escape is the tool error `"path outside sandbox root"`.

| Tool | Returns | Errors |
|---|---|---|
| `write_file(path, content)` | `{"path", "bytes"}` | outside root; content over 256 KiB; path is a directory |
| `read_file(path)` | text | outside root; missing; not a file; over 256 KiB |
| `list_files(path=".")` | sorted names, directories end with `/` | outside root; missing; not a directory |
| `run_python(path, args=None, timeout_s=60)` | `{"returncode", "stdout", "stderr", "timed_out", "oom_killed", "duration_s"}` | outside root; missing; `sandbox unavailable: ...`. `timeout_s` is capped at 120. One job at a time (`asyncio.Lock`). |
| `publish_site(subdir, name)` | `{"url", "files", "bytes"}` | invalid name; outside root; source has a symlink or special file (named); over 200 files or 20 MiB (counts given). Nothing is copied on error. |

`publish_site` copies regular files into `paths.sites_root/<name>/` (`name` matches `^[a-z0-9][a-z0-9-]{0,39}$`). It copies to a hidden temp dir under `sites_root`, then renames, so a republish replaces the old site without mixing files; a failed publish leaves the old site. `sites_root` is created with mode 0755 if missing, and the server writes nowhere else outside the sandbox root. A read-only static server (`voice-agent-sites.service`, ticket 16) serves it.

### Sandbox model

`voice_agent.sandbox.runner.run_in_sandbox` runs the script as:

`systemd-run --user --scope --quiet -p MemoryMax=512M -p CPUQuota=100% timeout <s> bwrap <binds> --chdir /sandbox --unshare-all --die-with-parent --new-session python3 <script>`

- **Filesystem:** only `/usr` (read-only), the `/bin` `/lib` `/sbin` symlinks (or read-only binds on a non-merged host), `/etc/alternatives` (read-only), a fresh `/proc`, `/dev`, `/tmp` (tmpfs) and the sandbox folder at `/sandbox` (read-write). `$HOME` is not mounted. The bind list is built from what exists on the host (`detect_layout`): a missing `/lib64` is skipped, a symlink into `/usr` becomes `--symlink usr/<x> /<x>`.
- **Network and PIDs:** `--unshare-all` removes network, PID, IPC and UTS access. `SandboxPolicy.network=True` adds `--share-net`; the code server never sets it.
- **Limits:** memory (`MemoryMax`), one core (`CPUQuota`), wall clock (`timeout`, exit 124, plus a host-side kill 5 s later). Output over 64 KiB per stream is cut with `…[truncated N bytes]`. An exit of 137 or SIGKILL is reported as `oom_killed` (best effort).
- **Environment:** the child gets only `PATH`, `LANG`, `LC_ALL`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS` and `HOME=/sandbox`, so API keys in the server's environment never reach scripts.
- Python packages the scripts need must be under `/usr`, or passed as `SandboxPolicy.ro_binds`.

### Pi prerequisites

- `sudo apt install bubblewrap` (the dev host has no `bwrap`; `run_python` then returns `sandbox unavailable: install bubblewrap`).
- A user systemd session (`loginctl enable-linger <user>`); without one, `run_python` returns `sandbox unavailable` with the `systemd-run` error.
- The memory cgroup controller: `memory` must appear in `/sys/fs/cgroup/user.slice/cgroup.controllers`. If not, add `cgroup_enable=memory` to `/boot/firmware/cmdline.txt` and reboot. Until then `run_in_sandbox` refuses unless `allow_no_memory_limit=True`.
- `preflight()` reports all of the above in `problems`. The isolation tests (`tests/sandbox/test_run.py`, marker `pi`) run only on the Pi with `VOICE_AGENT_PI=1`.

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

