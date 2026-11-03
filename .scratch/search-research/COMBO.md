# Idea 4: google_search + function tools in ONE request (2026-11-03, gemini-3.1-flash-lite, generateContent, Manila)

12 calls (ledger.jsonl, variants combo-*), about 7 Google searches.

- Needs `toolConfig.includeServerSideToolInvocations: true`. Without it: HTTP 400 "Please enable tool_config.include_server_side_tool_invocations to use Built-in tools with Function calling." (0.26 s).
- The model picks the right tool by itself (6 questions): changing facts -> searches (NBA 2.14 s with 2 searches, eclipse 1.73 s, Health Secretary 1.66 s); settled fact -> no search (0.76 s); weather -> `functionCall weather{place, day}` (0.68 s); home command -> `functionCall smart_home` (0.65 s). Answers correct (Knicks 2026, 2027-08-02, Mercado).
- Function round trip works: replay the returned model content, add the functionResponse, 2nd request 0.84 s with the right text.
- Streaming (streamGenerateContent, alt=sse): first text 1.39-1.44 s, end 1.63-1.87 s on searched questions (0.3-0.4 s earlier than waiting for the end).
- Today a Gemini-routed web turn is: decide the tool call (~0.7 s) + grounded web_answer (~2.1 s) = ~2.8 s (the stress run measured 2.95 s median first sound on web_answer turns). One request: 1.7-2.1 s, first words ~1.4 s when streamed. Estimated saving 0.7-1.4 s on those turns (inference from separate measurements, not measured end to end).
- Plumbing: google-genai 2.33.0 has ToolConfig.include_server_side_tool_invocations; Pipecat's GoogleLLMService takes `tool_config` and ToolsSchema(custom_tools={AdapterType.GEMINI: [{"google_search": {}}]}). Not tried inside Pipecat: how it replays the thought-signature and server-tool parts, and streaming text.
- Costs/risks: hedged mode sends two requests per turn, so both would search (double the searches; the tool gate cannot stop a built-in search). `web_answer` becomes redundant for the model, so the system prompt (Case B) must change and be re-measured with the dry run (the 10-30 postmortem: estimate first). The router path (wiki_summary -> web_answer) is unchanged.

## Prototype `--builtin-search` (agent_server flag), live A/B, 2026-11-03, n=11 web-type questions + 5 others
A = today's Gemini path (decides a web_answer call, which runs grounded Gemini through MCP); B = `--builtin-search` (Google Search in the model's own request). Same questions, alternated, no router, no hedging, warm connections, one run each.

| web-type questions (n=11) | A (now) | B (one request) |
|---|---|---|
| time to the end of the reply, median | 2.46 s (range 1.39-5.05) | 1.76 s (0.69-2.42) |
| first text | a "Checking..." line at 1.3-1.5 s, answer at the end | the answer itself, median 1.57 s (0.59-2.00) |
| settled facts (capital of Australia/Canada, Noli Me Tangere) | called web_answer anyway: 5.05 s, 1.39 s, 1.42 s | answered directly: 0.95 s, 0.83 s, 0.69 s |

Weather, lights and the umbrella question gave the same tool calls and the same answers on both (0.8-1.4 s first text, within 0.3 s of each other). B handled the 2087 Nobel and the gold price like A. Both gave the BSP rate as 5.50 % after a 50 bp hike, which disagrees with an independent search (6.25 % held); unresolved.

Gaps: the built-in searches are not in our ledger (they run inside the model request; the cost control only covers the MCP path, so the Google billing page is the only count); no router in this test; no Pipecat history replay test across turns (each turn is reset); n=1 per cell.

## Dry run of the `--builtin-search` prompt (2026-11-03; spend: dry-run ledger $0.114 for 7 runs, plus the diagnostic below: 16 calls, 11 searches, about $0.02 of tokens; total at the paid rate $0.29 against a $0.50 cap; 13 searches in the shared monthly ledger)
Pre-registered rule: every changing-fact turn searched; settled facts, weather, McCarthy (search_docs first), home and plain turns not searched and routed as before; no turn without an answer.

| Prompt variant | Targeted pack changing-fact turns searched | Held-out changing-fact turns searched |
|---|---|---|
| P3s: Case B bullet only, date at the end | 1/4 | not run |
| P4: stronger bullet + closing reminder | 1/4 | 1/6 |
| P5: date and "your memory is out of date" as the first lines | 1/4 | 0/6 (new questions) |

- **Fails the rule.** Routing was right on 14-15 of 15 turns in every variant (McCarthy went to `search_docs` first, weather and home commands unchanged, nothing searched that should not be). One miss: with P5 "Should I bring an umbrella?" was answered without calling `weather` (P3s and P4 called it).
- **Unsearched changing facts are invented or stale**: "iOS 27.2", a French Open 2026 winner, a crude-oil price "as of November 3", gold at $3,950. Several happened to match the truth (Senate President, Android 17), which hides the risk.
- **Cause (diagnostic, 4 questions per cell, the exact pipeline config replayed through the SDK):** pipeline as is (full prompt, 8 function tools) searched 1/4; short prompt with all 8 tools 3/4; full prompt with 2 tools 3/4; short prompt with 2 tools (my REST test) 4/4. A long prompt and many tools each make the model less willing to search; together they nearly stop it. Small n, but the order is consistent with the 25- and 40-question REST results, which used the short prompt and 2 tools.
- **Counter:** built (`search_count.py`), counts at the chunk where Google reports the queries (a first version that counted after the stream ended recorded nothing, because Pipecat stops reading after the last chunk), shares the monthly ledger with `web_answer`, takes `google_search` out of the request at the cap, and is priced into the dry run at $0.014 per search.
- **Conclusion:** keep `--builtin-search` off. The 1.76 s vs 2.46 s A/B win is real when the model searches, and the 11 A/B questions were mostly ones it did search; the dry run shows it does not do so reliably in the real prompt. Options if the idea is pursued: a short system prompt with the case rules moved into tool descriptions, fewer tools in the request, or a router/prefilter that sends changing-fact questions down the `web_answer` path. Each needs its own measurement.
