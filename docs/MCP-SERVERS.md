# MCP servers

Stdio MCP servers (FastMCP, `mcp>=1.2,<2`) that expose the agent's capabilities to subagents as tools. Each runs as `python -m voice_agent.mcp_servers.<name>`. Servers never write to stdout except MCP protocol; logging goes to stderr.

> Status: this design still needs the instructor's sign-off (DESIGN.md human checkpoint 1).

Tool names the agent sees are `<server>__<tool>` (for example `code__run_python`); see "Client manager" below.

## Path confinement (shared by every server)

`voice_agent.mcp_servers._paths` is the only way a server turns a tool-supplied path into a filesystem object. Every failure of the confinement check raises `PathEscapeError("path outside media root")`; the speech server re-raises that as `"path outside media root"` and the code server as `"path outside sandbox root"`.

### `resolve_inside(root, rel, *, must_exist)` (lexical and resolved check)

- `rel` must be a relative string. Rejected: empty, NUL byte, absolute (POSIX or Windows anchor), any `..` component (also after turning `\` into `/`).
- A **symlinked root is refused**. If `root` itself is a symlink, every call fails with the escape error, even for harmless paths. A Pi whose `~/agent_sandbox/media` (or a future `~/Music`) is a symlink to a USB drive will therefore refuse every call; mount the drive at the real path (or bind-mount) instead. A symlinked *ancestor* of the root is tolerated (the root is resolved), subject to the trust anchor below.
- The joined path is resolved with symlinks; the result must be `root` or inside it (`is_relative_to` on resolved paths, so `media2` is not mistaken for `media`).
- `must_exist=True` and missing gives `FileNotFoundError`.

This check is by path and is therefore racy on its own. The fd helpers below are what actually touch the filesystem.

### fd-based helpers

All three call `resolve_inside(..., must_exist=False)` first, then walk `rel` one component at a time with `os.open(part, O_DIRECTORY|O_NOFOLLOW, dir_fd=...)`, so a symlink swapped in at any component (`ELOOP` or `ENOTDIR`) is refused with the escape error instead of followed.

| Helper | Behavior |
|---|---|
| `open_dir(root, rel)` | Returns an fd for the directory; `rel` of `.` returns the root fd. The caller closes it. |
| `open_regular_for_read(root, rel)` | Opens the last component with `O_NOFOLLOW|O_NONBLOCK`, then `fstat`; a non-regular file (directory, FIFO, device) raises `IsADirectoryError`. Returns a binary file object. |
| `write_file_atomic(root, rel, writer, *, overwrite)` | Creates missing parent directories (mode 0755) during the walk. Writes to a temp file `.<name>.<16 hex>.tmp` in the target directory (`O_EXCL|O_NOFOLLOW`, mode 0644), then publishes it: `os.replace` when `overwrite`, otherwise `os.link` (so an existing target raises `FileExistsError` and is never clobbered). The temp name is always unlinked afterwards. A failed write leaves the old file untouched. |

**Root fd and trust anchor.** The root is opened per call, not remembered. `set_trust_anchor(path)` (the speech server sets it to `paths.sandbox_root`) makes the walk start at the anchor and descend to the root component by component with `O_NOFOLLOW`, so the root's own components below the anchor cannot be swapped for a symlink by sandboxed code. Without an anchor, or for a root outside it, only the last component is opened with `O_NOFOLLOW`; anything above that is trusted. `ELOOP`/`ENOTDIR` while opening the root is the escape error.

## speech

Run: `python -m voice_agent.mcp_servers.speech [--stt-backend NAME] [--tts-backend NAME] [--media-root DIR] [--max-audio-s 120]`

| Flag | Default |
|---|---|
| `--stt-backend` / `--tts-backend` | `agent.stt_backend` / `agent.tts_backend` from `config/agent.toml`; `fake` in tests |
| `--media-root` | `paths.media_root` |
| `--max-audio-s` | 120. Must be finite and greater than 0, otherwise the server exits with an argparse error (status 2). |

Backends are created on first use through the `audio_engine` registry (see `AUDIO-ENGINE.md`). Non-fake backends get `api_key` from the loaded config secrets (`deepgram`, `cartesia`), or none if the config cannot be loaded.

### Tools

| Tool | Returns | Errors (all become tool errors; the server keeps running) |
|---|---|---|
| `transcribe_audio_file(path)` | `{"text", "language", "duration_s"}` | `path outside media root`; `file not found: <path>`; `not an audio file: <path>`; `audio file too large: <path>`; `audio too large: <path>`; `audio too long: X.Xs exceeds Ns limit`; `speech backend error: ...` |
| `synthesize_speech_to_file(text, path, voice=None, overwrite=False)` | `{"path", "duration_s", "sample_rate"}` | `path outside media root`; text over 2 000 chars; voice invalid or over 128 chars; `output path must end in .wav`; `output path is a directory`; `output file exists: <path> (pass overwrite=true)`; `cannot write output file`; `speech backend error: ...` |
| `get_tts_voices()` | `[{"id", "name", "language"}]` | `speech backend error: ...` |

**Input limits for `transcribe_audio_file`.** Only WAV or WAVEX containers are accepted (other formats, even if libsndfile can read them, are `not an audio file`). At most 2 channels, a sample rate of at most 48 kHz, a file of at most 64 MiB, a decoded size (`frames * channels * 4`) of at most 64 MiB, and a duration of at most `--max-audio-s`. The file is opened with `open_regular_for_read` (no symlinks, regular files only) and the same handle is used for the checks and the read.

**Output rules for `synthesize_speech_to_file`.** The path must end in `.wav` (case-insensitive). `voice` must be printable, contain no `/` or `\`, and be at most 128 characters. Checks run before the backend is called, so a rejected request costs no API call. The file is written with `write_file_atomic`; missing parent directories are created, only inside the media root.

**Backend errors are sanitised.** The tool error is only `speech backend error: <ExceptionType>` plus ` (status N)` when the backend error carries an integer HTTP status. The backend's message never reaches the model. It is written to stderr only after redaction: URL query strings and fragments are dropped, `key=`, `api_key=`, `token=`, `access_token=` and `authorization=` values are replaced by `[redacted]`, and any prefix (6 characters or longer) of a configured secret, in raw, percent-encoded and plus-encoded form, is replaced by `[redacted]`. If the config cannot be loaded the detail is not logged at all. Backend error types come from `audio_engine._ws.SpeechBackendError`; if that module is absent a placeholder type is used.

### Resource

`speech://voices`: markdown list of the same voices as `get_tts_voices`.

## code

Run: `python -m voice_agent.mcp_servers.code [--root DIR]` (`--root` defaults to `paths.sandbox_root`; created if missing). Site URLs use `sites_host` and `sites_port` from `config/agent.toml`. The server refuses to start if `paths.media_root` contains a `..` component.

Tool paths go through the fd helpers against the sandbox root; an escape is the tool error `"path outside sandbox root"`. **Every tool takes the same `asyncio.Lock`**, so only one code tool runs at a time (not only `run_python`).

| Tool | Returns | Errors |
|---|---|---|
| `write_file(path, content)` | `{"path", "bytes"}` | outside root; content over 256 KiB (UTF-8 bytes); path is a directory. Atomic (temp file then `os.replace`), creates parents, always overwrites. |
| `read_file(path)` | text (UTF-8, invalid bytes replaced) | outside root; missing; not a file; over 256 KiB (`file too large to read`) |
| `list_files(path=".")` | sorted names, directories end with `/` | outside root; missing; not a directory |
| `run_python(path, args=None, timeout_s=60)` | `{"returncode", "stdout", "stderr", "timed_out", "oom_killed", "duration_s"}` | see below |
| `publish_site(subdir, name)` | `{"url", "files", "bytes"}` | see below |

The file tools (`write_file`, `read_file`, `list_files`) are not masked from the media folder: only the sandboxed script's view of media is hidden (see "Media mask"). `list_files` does not follow symlinks when classifying entries (a symlink to a directory is listed without `/`).

### `list_files` caps

At most 1 000 entries and 32 KiB of names (UTF-8 bytes plus one per entry). Past either cap the listing stops and a final entry `…[truncated: more entries than the listing limit]` is appended to the sorted result. A real file whose name starts with the same marker is listed as `./<name>`, so the marker cannot be forged.

### `run_python`

`timeout_s` is clamped to 1..120. Errors (all tool errors):

- `path outside sandbox root`, `file not found`, `not a file`.
- `sandbox unavailable: ...` (bwrap or systemd-run missing, memory controller missing, or the systemd user manager unreachable).
- `refusing to run: the media folder cannot be verified` (media path has `..` or cannot be resolved).
- `refusing to run: <rel> inside the sandbox is a symlink` (any component of the media folder's path inside the sandbox is a symlink).
- `sandbox folder is over the limit: ...` (disk check, below).

**Media mask.** If `paths.media_root` is inside the sandbox root (lexically, or after resolving), its root-relative path is passed as a hidden path and mounted as an empty `--tmpfs /sandbox/<rel>`, so scripts cannot read or write the speech server's files or swap the folder for a symlink. If media is outside the sandbox nothing is masked. `build_command` itself rejects hidden paths that are absolute, empty or contain `..`.

**Disk check.** Before every run `_dir_usage` measures the sandbox (allocated blocks, `st_blocks * 512`, symlinks not followed; the scan stops at 200 000 entries or once past the limit). The number is a lower bound: unreadable subtrees and entries past the scan cap are skipped rather than guessed, so a script cannot lock the sandbox out by making an unreadable directory. Directories owned by the server user that lack `u+rwx` are chmod-repaired so they count. If usage is above `max_dir_bytes` (256 MiB) the script is not started and the tool returns `sandbox folder is over the limit: at least N bytes used, limit 268435456; shrink large files by overwriting them with write_file (list_files shows what exists), then retry`. This is recoverable: `write_file`, `read_file` and `list_files` are not subject to the check, and overwriting a file with `write_file` shrinks it. The same measurement runs after the script; if the folder ends over the limit the same error is returned instead of the run result. In addition each script gets `prlimit --fsize=<max_dir_bytes>`, a per-file size cap.

**Result fields.** `timed_out` is true when the host-side kill fired (`timeout` plus 5 s grace; the returncode is then forced to 124), or when `timeout` returned 124 after bwrap had started (bwrap writes a byte to `--info-fd` once running) and the run lasted at least `timeout_s`. `oom_killed` is true only when the returncode is 137 or -9, `timed_out` is false, and the `oom_kill` counter in `user@<uid>.service/memory.events` rose during the run (or the counter is unreadable, in which case a SIGKILL is assumed to be an OOM kill, best effort). A script can still forge both by its own exit code or by killing itself; treat them as hints.

### `publish_site`

Copies regular files from a sandbox subfolder into `paths.sites_root/<name>/` (`name` matches `^[a-z0-9][a-z0-9-]{0,39}$`, else `invalid site name`). The URL is `http://<sites_host>:<sites_port>/<name>/`.

- Rejected: the whole sandbox root (`cannot publish the whole sandbox folder: name a subfolder`), a folder that contains, equals or lies inside the media folder (`cannot publish a folder that overlaps the media folder`), a non-directory.
- Entries whose name starts with `.` are skipped. Any other symlink is an error (`cannot publish symlink: <rel>`), as is any special file (`cannot publish special file: <rel>`).
- Caps: 200 files and 20 MiB total (`site too large: N files, M bytes (limits ...)`), 100 directories (`site has too many directories`), depth 8 (`site too deep`).
- The source is read with an fd walk (`O_NOFOLLOW` per component); destination files are created with `O_EXCL`, mode 0644, directories 0755.
- It copies into a hidden `.tmp-*` dir under `sites_root`, then renames the existing site aside (`.old-*`), renames the new one into place and deletes the old one. A failure restores the old site; nothing partial is published. `sites_root` is created with mode 0755 if missing (and removed again if the first publish fails).
- The copy runs in a worker thread while the lock is held. If the tool call is cancelled the server waits for the thread to finish before releasing the lock, so a cancelled publish cannot interleave with the next one.

A read-only static server (`voice-agent-sites.service`, ticket 16) serves `sites_root`.

### Sandbox model

`voice_agent.sandbox.runner.run_in_sandbox` runs the script as:

```
systemd-run --user --scope --quiet -p MemoryMax=512M -p CPUQuota=100% -p TasksMax=64 -p MemorySwapMax=0 \
  timeout <s> prlimit --fsize=<max_dir_bytes> \
  bwrap --ro-bind /usr /usr <merged-usr symlinks or ro-binds> [--ro-bind /etc/alternatives ...] \
        --proc /proc --dev /dev --tmpfs /tmp --bind <root> /sandbox [--ro-bind <extra>] [--tmpfs /sandbox/<media>] \
        --chdir /sandbox --unshare-all [--share-net] --info-fd <n> --die-with-parent --new-session \
        --clearenv --setenv PATH /usr/bin:/bin --setenv HOME /sandbox --setenv LANG C.UTF-8 \
        python3 /sandbox/<script> <args>
```

- **Filesystem:** only `/usr` (read-only), the `/bin` `/lib` `/sbin` (and `/lib64`) symlinks, or read-only binds on a non-merged host, `/etc/alternatives` (read-only), a fresh `/proc`, `/dev`, `/tmp` (tmpfs), and the sandbox folder at `/sandbox` (read-write). `$HOME` is not mounted. The bind list is built from what exists on the host (`detect_layout`).
- **Network and PIDs:** `--unshare-all` removes network, PID, IPC and UTS access. `SandboxPolicy.network=True` adds `--share-net`; the code server never sets it. **Known limitation (L2):** with `network=True` the sandbox shares the host network namespace, so host loopback services (for example the ME2 dashboard on `127.0.0.1:8000`) and abstract unix sockets are reachable. Do not enable it for untrusted scripts.
- **Limits:** memory (`MemoryMax`, swap disabled), one core (`CPUQuota`), at most 64 tasks (`TasksMax`), wall clock (`timeout`, exit 124, plus the host-side kill 5 s later), per-file size (`prlimit --fsize`), and the 256 MiB folder budget above. Output over 64 KiB per stream is cut with `…[truncated N bytes]`.
- **Environment, two layers.** (1) The `systemd-run` process gets only `PATH`, `LANG`, `LC_ALL`, `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` from the server's environment, plus `HOME=/sandbox`. It needs the last two to reach the user manager, because MCP servers are spawned with a minimal env. If `XDG_RUNTIME_DIR` is unset it falls back to `/run/user/<uid>` when that directory exists and is owned by the user; if `DBUS_SESSION_BUS_ADDRESS` is unset and `$XDG_RUNTIME_DIR/bus` is a socket, it is set to `unix:path=<that socket>`. (2) The script itself runs after `--clearenv` and sees only `PATH=/usr/bin:/bin`, `HOME=/sandbox` and `LANG=C.UTF-8`, so API keys in the server's environment never reach it.
- Python packages the scripts need must be under `/usr`, or passed as `SandboxPolicy.ro_binds`.
- A `systemd-run` failure before bwrap started (`Failed to connect to`, `Failed to create bus`, `Failed to start transient`) is reported as `sandbox unavailable: <stderr>`, not as a script failure.

### Pi prerequisites

- `sudo apt install bubblewrap` (the dev host has no `bwrap`; `run_python` then returns `sandbox unavailable: install bubblewrap`). `prlimit` comes from `util-linux`.
- A user systemd session (`loginctl enable-linger <user>`, which also creates `/run/user/<uid>` at boot); without one, `run_python` returns `sandbox unavailable` with the `systemd-run` error.
- The memory cgroup controller must be delegated to the user manager: `memory` must appear in `/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/cgroup.subtree_control`. If that file is unreadable, `user.slice/cgroup.controllers` is used instead. If it is missing, add `cgroup_enable=memory` to `/boot/firmware/cmdline.txt` and reboot. Until then `run_in_sandbox` refuses unless `allow_no_memory_limit=True`.
- A media or sandbox folder that is a symlink is refused (see "Path confinement"); the deploy preflight (ticket 16) should report it.
- `preflight()` reports bwrap, systemd-run, prlimit and the memory controller in `problems`. The isolation tests (`tests/sandbox/test_run.py`, marker `pi`) run only on the Pi with `VOICE_AGENT_PI=1`.

### Unverified until Pi checkpoint

Everything above comes from reading the code and unit tests with fakes. The dev host has no `bwrap`, so these claims have not been run on real hardware and must be checked at the human Pi checkpoint:

- [ ] bwrap isolation actually holds: no network, no `$HOME`, no host PIDs, `/sandbox/<media>` is the empty tmpfs, and a script cannot see or write outside `/sandbox` and `/tmp`.
- [ ] `prlimit --fsize` works together with `bwrap` inside a `systemd-run --user --scope` (the limit is inherited by the script).
- [ ] `/run/user/<uid>` exists for the service user, which needs `loginctl enable-linger <user>`; and the `XDG_RUNTIME_DIR`/DBUS fallback lets `systemd-run --user` connect when launched by the MCP client manager.
- [ ] Python's `SIGXFSZ` under `--fsize` actually surfaces as `OSError: [Errno 27] File too large` (EFBIG) inside the script, rather than killing it with returncode 153.
- [ ] The delegation path (`user@<uid>.service/cgroup.subtree_control`) matches the Pi's cgroup layout, and the `memory.events` `oom_kill` counter is where `oom_killed` expects it.
- [ ] `MemoryMax=512M`, `MemorySwapMax=0` and `TasksMax=64` are enforced on the Pi's kernel.

## Client manager

`voice_agent.agent.mcp_clients.McpClientManager` starts the configured servers over stdio and returns a `ToolRegistry`; tools are named `<server>__<tool>`. It is the harness-side counterpart of the servers above.

- A server that cannot start (or needs an unset `${VAR}` in its env) is listed in `registry.unavailable` with a reason; the others still work. Start-up timeout is 10 s per server.
- Calls to one server are serialized. Default timeout 30 s per call; `code__run_python` gets 130 s (more than the tool's 120 s cap).
- A timeout, whether from the local `asyncio` wait or the MCP session's own read timeout (an `McpError` with code 408), returns the text `timed out` with `is_error`.
- If a server died, it is restarted once and the call retried; if the restart fails the text is `server <name> unavailable`. Other exceptions return `<Type>: <message>` cut to 500 characters.
- **Rendering cap:** the result text is the concatenation of text blocks (non-text blocks become `[<type> omitted]`), truncated to 16 KiB (16 384 characters) with a trailing `[truncated N chars]`. This is what reaches the model; the servers' own caps (64 KiB output, 256 KiB files) are larger.

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


## smart_home tool (pipeline/, not an MCP server)

One Pipecat function, `smart_home(intent, slot=None)`, covers all 19 ME2 intents by posting to the dashboard's `POST /api/command`; it lives in the separate Pipecat project `pipeline/` (`voice_pipeline.tools.smart_home`). The `home` server above is unchanged. See `docs/SMART-HOME-TOOL.md`.

## The agent's real tools (2026-10-24; `pipeline/`, plan `.scratch/mcp-set/PLAN.md`)

These are the MCP servers the live agent (`make agent-server`) loads, wired in `pipeline/src/voice_pipeline/agent_tools.py` (`standard_servers()` returns the required and optional servers, and `build_tools()` gives the Pipecat tools). They are separate from the design above: the agent calls each tool by its plain name (`weather`, not `web_info__weather`). An optional server that can't be reached is left out with a warning, and the agent still starts. The dry run skips prompts whose tool is missing.

| Server | Transport | Tools | Notes |
|---|---|---|---|
| `docs` (`src/voice_agent/mcp_servers/docs.py`, fastmcp 2.14.7) | stdio | `search_docs` | BM25 over the McCarthy corpus; a sample server, kept |
| time (official `mcp-server-time==2026.10.19`, in `pipeline/pyproject.toml`) | stdio, `python -m mcp_server_time --local-timezone Asia/Manila` | `get_current_time`, `convert_time` | took TIME out of `smart_home` (the owner: the requirement is the ME2 phrases, not ME2's structure) |
| `web_info` (`src/voice_agent/mcp_servers/web_info.py`, fastmcp 2.14.7) | stdio (root project) | `weather(place=None, day="today")` (Open-Meteo geocoding + forecast; default place `VOICE_AGENT_HOME_PLACE`, "Cebu City"); `nearby(kind, place=None)` (the three closest restaurants, cafes, pharmacies, ATMs and so on, nearest first with distance and street, via Photon's reverse search on OpenStreetMap, about 1.3 s; with a Google Maps key file at `~/.secrets/google-maps/api-key.txt` or `$GOOGLE_MAPS_KEY_FILE` (Places API (New) enabled) restaurants, cafes, bars, fast food and hotels come from Google's popularity-ranked Nearby Search with ratings (at least 10 ratings, nearest three; nearest-first returned unrated pins), and a refused key (401/403) is not retried; pharmacies, ATMs and the rest stay on Photon; Overpass was tried and took 24 s or timed out; `VOICE_AGENT_HOME_COORDS="lat,lon"` pins the venue, passed to this server on its command line because MCP children do not inherit the environment, and the agent server looks the barangay up once at boot from the same point (Nominatim, about 1 s; without a pin it says "around Barangay X" from the city centre)); `nearest(name, place=None)` (the nearest place with a given name or chain, e.g. Jollibee: Google text search with ratings, Photon name search without a key; spoken as it is); `wiki_summary(topic)` (Wikipedia search + REST summary, at most 3 sentences / 600 characters) | keyless; 4 s timeouts; errors come back as one sentence the model can say. Keeps one client for the server's life, remembers place lookups, and reuses a forecast for 10 minutes. Both Open-Meteo connections are opened at start and touched every 20 s while the server is in use (up to 10 min after the last lookup): a new place took 2.7-5.7 s on cold connections (6.6 s after 35 s idle) and 0.54-0.64 s kept warm. The User-Agent must carry a contact URL, or Wikimedia answers 403. Took WEATHER out of `smart_home` |
| `pi_device` (`scripts/pi_device_mcp.py`, `mcp` 1.30 FastMCP, **runs on the Pi**) | streamable HTTP, port 8790, `http://169.254.230.219:8790/mcp` (`--pi-url`, `''` for none) | `get_volume`, `set_volume(percent)` (clamped 0-100, `wpctl` on the CD002 sink, argument list, no shell), `device_status` (CPU temperature, uptime, load, free memory, speaker connected) | **security-sensitive:** bearer token from `LAN_SPEECH_TOKEN` (the session token, compared with `hmac`; refuses to start without one). DNS-rebinding protection stays on, with the allowed Host list built from `hostname -I` (without it, mcp 1.x accepts only localhost and the laptop gets `421 Invalid Host header`) |

**Routing** is in `pipeline/config/eval/system_agent.txt`:
- `smart_home` (17 intents now): home commands, including relative music volume.
- time, weather and general knowledge: the real tools above.
- The speaker volume with a number, and the Pi's status: `pi_device`.

**Results** (`.scratch/mcp-set/PLAN.md`):
- Dry run with the Pi reachable, two runs: 88/92 exact in both, device prompts 5/5.
- Spoken check: 8/8 questions routed right, but end of speech to first sound took 3.5-10.9 s (see `docs/LATENCY.md`).

**Start the Pi server** (ask the owner first; the Pi runs no model):
```
ssh -i ~/.ssh/id_ed25519_pi rpi-anavarez@169.254.230.219 'cd ~/Documents/AI-231-ME3 && LAN_SPEECH_TOKEN=$(cat ~/.config/lan_speech_token) timeout 7200 .venv/bin/python -u scripts/pi_device_mcp.py --port 8790'
```
The agent server needs the same token in its environment (`LAN_SPEECH_TOKEN=... make agent-server`).

## tavily_search (web answers, prototype)

`src/voice_agent/mcp_servers/tavily_search.py`, started by the agent server over stdio (`agent_tools.TAVILY_COMMAND`). One tool, `web_answer(query)`: Tavily's search API (`POST https://api.tavily.com/search`, `include_answer: basic`, 3 results) returns a short answer written from live web results; the server says the first two sentences (at most 400 characters) and drops a lead-in such as "According to the provided source,". It replaces the Wikipedia lookup for general-knowledge questions; Wikipedia (`web_info.wiki_text`) is the fallback when there is no key, Tavily errors, times out (3.5 s in all) or returns nothing, so a lookup is never worse than before.

- **Key:** `$TAVILY_API_KEY`, else `~/.secrets/tavily/api-key.txt` (`$TAVILY_KEY_FILE` overrides). Read at run time, sent only as the `Authorization` header, never logged or returned. The question text is sent to Tavily.
- **Depth:** `fast` by default (`$TAVILY_DEPTH`). Fresh queries from Manila, 4 each on 2026-10-29: `basic` 2.35 s median, `fast` 1.21 s, `ultra-fast` 1.24 s; a repeated query is cached by Tavily (about 0.25 s). Wikipedia alone: 0.63 s.
- **Router:** it still writes `wiki_summary|topic` (trained that way); the agent server runs it as `web_answer` with the user's whole question (the topic alone when the turn had other calls), so no retraining is needed. Gemini sees `web_answer` and the system prompt prefers it; `wiki_summary` stays registered.
- **Gemini with Google Search grounding is the first backend (2026-11-03; `gemini_grounding.py`).** One request to `gemini-3.1-flash-lite` with the `google_search` tool, a system instruction that gives today's date and says to search for anything that could have changed (office holders, results, prices, "latest", "next") and not for settled facts. 25 questions from Manila, warm connection: **median 0.79 s** (p90 1.55 s) against Tavily's 1.33 s, searches on 3 of 25; settled facts 0.7-1.1 s, searched ones 1.6-2.1 s. With the date and the instruction, 5 of 8 changeable questions were searched and none of the settled ones, and the eclipse answer was right (2027-08-02; Tavily and the plain Gemini call said 2026-08-12, already past). Its answers are shorter and sayable, and it refuses a future year ("has not been awarded yet") without any check. It is not perfect: with no search it answered "last NBA championship" from memory (2024), the search results for that question disagree between runs, and its specific Gilas score cannot be checked. Order: Gemini (3 s limit) then Tavily then Wikipedia; `WEB_BACKEND=tavily` turns Gemini off; no Gemini key also turns it off. The answer check is not applied to Gemini's text (it hedged a correct reply on a name the answer did not repeat).
- **Cost control.** Google bills each search query ($14 per 1,000 beyond 5,000 free a month shared by Gemini 3 models; paid projects only, so check that prepaid credit counts). Every call is appended to `~/.local/state/voice_agent/web_answer_ledger.jsonl` (time, month, searches, tokens, seconds; not the question) and when the month's searches reach 4,500 the server goes straight to Tavily. 40 grounded calls on 2026-11-03 used 9 searches. The MCP server connects over IPv4 only (the IPv6 path to Google resets connections here).
- **Spoken acknowledgement for a slow lookup (2026-11-03).** When the router runs `web_answer` and it is still going 1.0 s after it started (`LOOKUP_ACK_S` in `agent_server.py`), the server sends the client a tool event and the client plays its pre-recorded line for that tool (`web_answer` in `config/fillers.json`, the same seven reviewed lines as `wiki_summary`). Fast lookups cancel the timer, so nothing plays. Simulated on 65 measured lookups (25 mostly-settled and 40 search-needed): for search-needed questions first sound 1.8 s to 1.2 s and answer start 2.8 s to 2.2 s (the generic 1.8 s stall used to play and queue the answer behind it); for the settled-heavy set the median is unchanged and the p90 answer start can grow by up to 0.5 s when an answer finishes while the line is still playing. Not measured in a spoken session. Weather is not acknowledged (about 0.5 s).
- **Prototype: the model searches itself (`agent_server --builtin-search` and `dryrun --builtin-search`, 2026-11-03). Off by default; not reliable enough to adopt.** Gemini's `google_search` goes in the same request as the function tools (`ToolsSchema` custom tool, `tool_config.include_server_side_tool_invocations`, which the API requires: HTTP 400 without it), `web_answer` and `wiki_summary` are hidden from the model but kept for the router, and `config/eval/system_agent_search.txt` (with `{today}` filled in) tells it to search for what can change. When the model does search, a live A/B on 11 questions finished the reply at 1.76 s median against 2.46 s through `web_answer`, and settled facts were answered in 0.7-0.95 s instead of 1.4-5.0 s. But a dry run (targeted pack, three prompt variants, 6-15 turns each) found it searched only 1 of 4 changing-fact questions and 0-1 of 6 held-out ones, and answered the rest from memory (an invented iOS version, a French Open winner, a crude-oil price). A replay of the exact request through the SDK pins it on the long prompt and the 8 function tools: 1/4 searched as is, 3/4 with either removed, 4/4 with both. Searches in this path are counted by `search_count.py` (shared monthly ledger with `web_answer`, priced into the dry run, `google_search` removed from the request at the cap). Full numbers: `.scratch/search-research/COMBO.md`. With `--hedge` both requests would search: use `--hedge -1`.
- **One search per question (2026-11-03).** The router's lookup (the user's words) and Gemini's own call (reworded, about a second later) used to be two searches. Wordings of a question are now compared by their content words (`content_words`: "When was the EDSA revolution?" = "the EDSA revolution"; equal, or at least 75 % overlap of two or more words), and a call that matches a search already running joins it (`asyncio.shield`, so a cancelled hedge loser does not stop it for the other). Gemini's call then waits for the router's search instead of starting its own, which is also the speculative prefetch.
- **The answer is checked against the question.** `unconfirmed(query, answer, data)` lists the numbers and names in the question that neither the answer nor any returned source mentions (numbers compared without commas; names with 0.8 spelling tolerance, so accents and variants pass). If any are missing the sentence is "I couldn't confirm 2087 in what I found. The closest result says: ..." ("Who won the 2087 Nobel Prize in physics?" used to get the 1987 laureates). The check has no list of years or topics; it applies to any question. Of 12 live questions, only the 2087 one was hedged.
- **Connection (measured 2026-11-03).** `api.tavily.com` is a plain EC2 host in Virginia (no CDN, no regional endpoint; Tavily's docs name none): round trip 0.22 s, TCP + TLS 0.45 s. Server time is most of a call: `fast` + answer 1.25 s total (server 1.03 s), `fast` without the answer 0.72 s, `ultra-fast` without the answer 0.52 s. Without Tavily's answer step the text is page boilerplate ("Title: ... We use cookies", a video description), so the answer step stays; `max_results` 1 saves 0.1 s and weakens the answer check, so it stays at 3. Nothing faster is on offer from the client side; the remaining levers are the keep-alive, the join above and the cache.
- **Tests:** `tests/mcp_servers/test_tavily_search.py` (mocked HTTP, fake key); `pipeline/tests/test_router.py` for the router path.
