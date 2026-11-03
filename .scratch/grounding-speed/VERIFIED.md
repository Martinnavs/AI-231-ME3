# Codex grounding-speed research, checked (2026-11-03)
Raw: CODEX-grounding.md (gpt-6-luna xhigh, live web, read-only).

| Codex claim | Check | Verdict |
|---|---|---|
| Stream into TTS, ~0.3-0.6 s earlier | our own COMBO.md numbers (first text 1.39-1.44 s, end 1.63-1.87 s, blocking median 1.98 s) | holds (an estimate from separate samples) |
| Priority tier supports gemini-3.1-flash-lite, costs 75-100 % more per token, "lower latency" | Google's priority-inference page: listed for 3.1 Flash-Lite; "75-100% more than Standard"; set `service_tier: priority`; no latency number; the page names the Interactions API (Codex said GenerateContent); the "Tier 2/3 only" condition is not on the page | partly verified; eligibility unknown |
| 3.5 Flash-Lite faster (2.162 vs 2.810 s) but ungrounded 11/24 vs 4/24 | cwest/gemini-search-mcp sweep (Vertex global, 24 cases, July 2026): numbers match | verified; our own dry runs (HANDOFF.md:21) found 3.5 the same speed with 3 stalls of 7-17 s -> do not switch |
| Speculative start on an interim transcript (arXiv 2610.07641: 5.79 -> 4.60 s) | paper title and numbers match | verified, but our own test dropped it: Kroko's early text equals the final text on 22 % of turns (OPTIMIZATION-RESEARCH.md:48) |
| Live API: audio-only output on gemini-3.8-live | not checked | not a drop-in for local TTS either way |
| No per-request search cap, no fast/lite grounding mode, no Manila region benchmark | NOT FOUND (Codex) | accept as not found |
