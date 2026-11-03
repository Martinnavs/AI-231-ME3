# agy search-alternatives report, checked 2026-11-03 (Manila, warm connections, 10 questions, one run each)

Script: verify.py (keys read at run time, never printed). Raw agy report: AGY-search-alternatives.md (mostly search snippets and vendor claims).

| agy claim | Measured here | Verdict |
|---|---|---|
| Wikipedia generator=search + extracts: 0.40-0.55 s from Manila, answers 65-75 % of questions | 0.71 s median (0.48-0.95), p90 0.93 s. Only 4/10 extracts answered the question (Mona Lisa, EDSA, Kilimanjaro, Eiffel); wrong page for the president (Cabinet of the Philippines), Iceland population (no number), World Cup winner (match description), Sinigang (Mama Sita's company), Nobel 2023 (controversies), 2087 (the year 1961). Our answer check flags 5 of the 6 failures (not the president, Iceland or World Cup ones). | Refuted as a first-answer hedge: it would speak wrong answers. Fine only as today's fallback. |
| DuckDuckGo Instant Answer fast, empty for conversational questions | 0.12 s median, non-empty for 1 of 10 (Sinigang) | True; useless for our questions |
| Tavily ultra-fast + answer: about 0.95 s, keep quality | 1.05 s median vs 1.23 s for fast, but 4/10 answers say "The sources do not provide..." (Mona Lisa, president, Nobel 2023, 2087); fast answered 9/10 and the 2087 one is now hedged | Refuted: faster by 0.2 s, but not usable |
| Gemini grounding 2.2-4.5 s (a Reddit snippet) | 1.33 s median (0.70-2.04), n=7, native endpoint, 7/7 correct, and it refuted 2087 ("has not yet been awarded") | agy's number was too high. Same speed as a Tavily call, so no gain as a tool backend; the gain would be Gemini answering in one call with grounding instead of tool call + Tavily. Grounded prompts are billed ($14/1000 after 5000 free a month, per agy's snippet, unverified). |
| Tavily needs HTTP/2 and a 40 s heartbeat; ALB idle 60 s | curl already negotiates HTTP/2; our server drops idle connections between 15 and 45 s, so the 20 s ping is right | Nothing to gain |
| Linkup flash sub-second, free $20/month | Not testable without an account; api.linkup.so is a US host (TCP 0.17 s, TLS 0.42 s, similar to Tavily) | Unverified, vendor claim only |

Connect times from here (curl -4, TCP / TLS): tavily 0.29/0.51, linkup 0.17/0.42, you.com 0.24/0.47 (all US); exa 0.08/0.27, serper 0.04/0.11, parallel 0.09/0.17, serpapi 0.07/0.15, brave 0.03/0.44 (edge near Asia; the compute behind it is unknown); wikipedia 0.05/0.12 and ddg 0.05/0.11 (edge near Asia).
