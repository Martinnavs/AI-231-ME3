# Plan: a real MCP tool set for the harness (then latency)

**Status:** confirmed; tickets 01, 02, 03, 05 done and integrated (2026-10-24); 04 (Pi deploy + checks) next, Pi actions need the owner's go. **Branch:** `agy/interrupts`. **Code:** agy from level-3 tickets (owner's choice); the assistant writes and tests the reference code first, dry-runs the tickets, builds the fence; the owner launches it.

## Why

`search_docs` (BM25 over the McCarthy corpus) is a sample MCP server; real voice assistants call tools that look things up or act on devices. The owner chose three real ones (2026-10-24): the official **Time** server, our own **Weather + Wikipedia** server (keyless public APIs), and a **Pi device** server reached over the network. The latency work (filler phrases at tool calls, model comparison; `.scratch/latency-research/SUMMARY.md`) comes after, measured on these realistic tool turns.

## Checked before planning

- `mcp-server-time` 2026.10.19 (official reference server, Python) resolves together with `pipecat-ai==1.13.0` (`uv pip compile`: mcp 2.3.0, onnxruntime 1.24.4 unchanged).
- Open-Meteo forecast and geocoding, and Wikipedia's REST summary, answer without an API key (one request each from this laptop).
- Pipecat 1.13's `MCPClient` takes `StdioServerParameters`, `SseServerParameters` or `StreamableHttpParameters`.
- The Pi (Python 3.12) already has the `mcp` SDK, which includes `mcp.server.fastmcp.FastMCP`: no new package on the Pi. It reaches PyPI, has `wpctl`, and exposes the CPU temperature in `/sys/class/thermal`.

## Tasks

| # | Task | Files | Depends on |
|---|---|---|---|
| 01 | **Weather + Wikipedia server** (fastmcp 2.14.7, stdio, like `docs.py`): `weather(place=None, day="today")` via Open-Meteo geocoding + forecast, default place from `VOICE_AGENT_HOME_PLACE` (default "Quezon City"); `wiki_summary(topic)` via Wikipedia search + REST summary, at most 3 sentences / 600 characters. 4 s timeouts; network errors come back as one short sentence the model can say | `src/voice_agent/mcp_servers/web_info.py`, `tests/mcp_servers/test_web_info.py` (HTTP mocked) | - |
| 02 | **Pi device server** (runs on the Pi; `mcp` SDK FastMCP, streamable HTTP on port 8790, bearer token): `get_volume()`, `set_volume(percent)` (clamped 0-100, `wpctl` on the CD002 sink, argument list, no shell), `device_status()` (CPU temperature, uptime, load, free memory, whether the speaker sink is present) | `scripts/pi_device_mcp.py`, `tests/test_pi_device_mcp.py` (commands faked) | - |
| 03 | **Agent wiring**: register Time (stdio `python -m mcp_server_time --local-timezone Asia/Manila`; `mcp-server-time` added to `pipeline/pyproject.toml`), web_info (stdio) and the Pi server (`StreamableHttpParameters` with the token; skipped with a warning if the Pi is unreachable, so the agent still starts); router system prompt updated; about 15 new dry-run prompts (time, weather, Wikipedia, volume, device status, plus "unreachable" cases) | `pipeline/src/voice_pipeline/agent_tools.py`, `pipeline/config/system_agent.txt`, `pipeline/config/eval/agent_prompts.jsonl`, `pipeline/pyproject.toml`, `pipeline/uv.lock`, tests in `pipeline/tests/` | 01, 02 (uses their real servers) |
| 04 | **Checks**: dry run (Gemini, costs cents) for routing on old + new prompts; deploy 02 to the Pi (**ask first**: copy one file, start it); one spoken session with a question per tool | results in this file | 03 |

01 and 02 touch disjoint files and neither consumes the other: they can be one agy wave. 03 consumes both (real tool names and schemas), so it runs after, against the landed servers. The dependency change in 03 (`mcp-server-time`) goes in first, by the assistant, so agy's work copy resolves offline.

**Security-sensitive:** 02 (a network-exposed server that changes device state): bearer token required (the session token, as `pi_speaker.py` does), volume clamped, no shell, read-only status otherwise. 01 sends user-chosen place names and topics to Open-Meteo and Wikipedia (no keys, no personal data beyond that).

**Non-goals:** timers on the Pi (the `smart_home` tool already has TIMER), the Memory and Fetch servers, any model on the Pi, removing `search_docs` (it stays as the sample).

## Decisions needed at confirmation

1. Add `mcp-server-time` to `pipeline/pyproject.toml` (and the lock)?
2. Default weather place: "Quezon City"? (used when the user gives none)
3. Pi server: port 8790, the same session token as the speech server and `pi_speaker`?

## After this: latency plan (outline, details later)

1. Filler at every tool call: a rotating set of about 7 natural phrases per tool (a default set for unknown tools), no immediate repeats, sent as the first spoken sentence when the tool call arrives (`on_function_calls_started`). The result still comes from the LLM.
2. Model comparison (cents): `gemini-3.1-flash-lite` against `gemini-2.5-flash` (thinking off) and `gemini-2.5-flash-lite`, first-token time and tool accuracy on the dry run.
3. Early decode with tail padding (offline), then the TTS first-chunk ladder (ticket 25).

## Results (2026-10-24)

- Waves 1 (01, 02) and 2 (03, 05) by agy: byte-identical to the reference, no deviations.
- Dry run on the reference (Gemini, 87 turns, Pi tools absent so the 5 device prompts were skipped): **85/87 exact**; time 3/3, weather 3/3, knowledge 3/3, mixed 4/4, all older kinds unchanged except the known "I'm cold" guess (a01) and one extra wiki_summary next to a correct search_docs answer (d08). No leaks.
- Weather was slow (tool call at 0.6-1.0 s, first text 3.7-5.2 s): Open-Meteo is in Germany (~270 ms RTT) and each request opened a new TLS connection. Ticket 05: repeat question 5 ms, new place on a warm connection 0.55 s; the first weather question of a session is still cold (~3 s): warming at start-up is a possible later change.
- Ticket 05 added after the plan was confirmed (a measured problem in its own scope); recorded here.

## Ticket 04 (2026-10-24, owner's go)

- `scripts/pi_device_mcp.py` copied to the Pi (checksums match) and started with the session token; `pi_speaker` restarted.
- **Found on the Pi:** the agent's client got `421 Invalid Host header`. The `mcp` 1.x SDK turns on DNS-rebinding protection for a FastMCP built with the default host and accepts only localhost Host headers; the laptop test had used 127.0.0.1. Fix (written by the assistant directly, owner's choice for a ~10-line change): `allowed_hosts()` builds the allow-list from `hostname -I` (the Pi's own addresses, any port) and the check stays on. Verified on the Pi: the agent's client lists get_volume, set_volume, device_status; a wrong token gets 401; a foreign Host header still gets 421.
- Dry run with the Pi reachable, two independent runs (`pipeline/out/agent_dryrun/mcp-live-pi`, `mcp-pi-1`): **88/92 exact in both, device 5/5 in both**, no leaks. Misses: a01 "I'm cold" (known), b05 "Call Gary" (the tool refused it correctly; the scorer counts the attempt), d08 (extra wiki_summary next to a correct search_docs answer), and one different flaky miss per run (p07 LIST_REMINDERS called four times; c16 a CALL with no name). The two volume prompts left the Pi at 20%; restored to 80%.
- Next: the spoken check (owner speaking), one question per tool.
