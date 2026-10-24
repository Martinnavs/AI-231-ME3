# DESIGN: real MCP tool set

**Context:** the agent (Pipecat 1.13, native Gemini) gets three real MCP servers next to `smart_home` and the sample `search_docs`; then latency work on those tool turns. Plan: `PLAN.md`. Status: draft, awaiting confirmation.

## Architecture (new in bold)

```mermaid
flowchart LR
    DC["doc_chat (laptop): mic, STT, TTS, guard"] -- "transcript (ws 8781)" --> AG["agent server: Pipecat + Gemini"]
    AG -- "spoken text" --> DC
    AG -- "smart_home(intent, slot)" --> SH["smart_home tool (in process)"]
    AG -- "MCP stdio: search_docs" --> DOCS["docs server (sample)"]
    AG -- "MCP stdio: get_current_time, convert_time" --> TIME["**mcp-server-time (official)**"]
    AG -- "MCP stdio: weather, wiki_summary" --> WEB["**web_info server (ours)**"]
    WEB -- "HTTPS, no key" --> OM["Open-Meteo, Wikipedia"]
    AG -- "**MCP streamable HTTP + token (cable)**" --> PI["**pi_device_mcp (on the Pi): volume, status**"]
    PI -- "wpctl, /sys, /proc" --> HW["Pi speaker sink, CPU"]
```

## Plan tasks

```mermaid
flowchart LR
    T1["01 web_info server"] -- "tool names + schemas" --> T3["03 agent wiring"]
    T2["02 Pi device server"] -- "tool names + schemas, URL" --> T3
    T3 --> T4["04 dry run + Pi deploy + spoken check"]
```

01 and 02 have no edge between them: one parallel wave. 03 consumes both: after.

## Decisions log

- 2026-10-24: real MCP set = Time (official), Weather + Wikipedia (ours), Pi device server; Memory/Fetch not now. MCP set before latency. Code by agy, level-3 tickets.
- 2026-10-24: `mcp-server-time` added to `pipeline/`; default weather place Quezon City; Pi server on port 8790 with the session token.
- 2026-10-24: TIME and WEATHER removed from `smart_home` (the requirement is the ME2 phrases, not ME2's structure); time, weather and general knowledge go to the real tools.
- 2026-10-24: speaker volume with a number and Pi status go to the Pi; relative music volume stays `smart_home` VOLUME_UP/DOWN.
- 2026-10-24: model stays `gemini-3.1-flash-lite` (Jev Router via OpenRouter: 2.94 s vs 0.98 s first token, 14/16 vs 16/16 right tool, 10x cost; `.scratch/latency-research/jev_check.py`).

## Findings index

- Weather tool ~3 s per question (fresh TLS to Open-Meteo in Germany, twice): fixed by ticket 05 (kept connection, remembered places, 10-min forecast cache).
