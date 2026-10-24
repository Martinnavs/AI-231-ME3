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

## Findings index

(none yet)
