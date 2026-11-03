# Streaming vs blocking grounded Gemini (written 2026-11-03 BEFORE the first request)
Same request as production (gemini_grounding.py: gemini-3.1-flash-lite, google_search, minimal thinking, the
production system prompt with today's date), one warm httpx client (a warm-up GET first), from Manila.
4 fresh changing-fact questions x {blocking generateContent, streamGenerateContent?alt=sse}, order alternated per
question, sequential, no retries. Measured from sending the request: blocking = response complete; streaming =
first complete sentence (text up to the first ". ", "! ", "? " or the end), plus first text and end.
PASS only if: (1) median streaming first-sentence time is at least 0.3 s earlier than median blocking time;
(2) every answer (both modes) is correct, checked by an independent web search; (3) every call shows a Google
search (webSearchQueries non-empty). Report searches and spend.
Budget: about $0.12 (<= 8 searches x $0.014 + tokens), cap $0.15, stop at 90 %.

## Result (2026-11-03, 7 of 8 calls; the 4th hit httpx.ReadError and was not re-sent) -> FAIL
| Question | Blocking (complete) | Streaming first sentence / first text / end | Search |
|---|---|---|---|
| Prime Minister of Japan | 1.53 s | 1.86 / 1.77 / 2.00 s | 1 / 1 |
| Brent crude | (read error) | 1.39 / 1.29 / 1.73 s | - / 1 |
| Latest Formula One race | 2.28 s | 2.32 / 2.20 / 2.38 s | 1 / 0 reported |
| Linux kernel | 1.44 s | 1.71 / 1.63 / 1.81 s | 1 / 1 |
Median blocking 1.53 s (n=3) vs streaming first sentence 1.79 s (n=4); paired (3 questions) streaming is 0.04-0.33 s
LATER every time. Rule 1 fails, so correctness was not checked further (the PM answer, Sanae Takaichi since
21 October 2025, is right). The answers are one or two short sentences and text starts only after the search, so
first text comes 0.1-0.2 s before the end: there is nothing to stream early. The earlier "1.4 s first text vs
1.98 s blocking" compared separate samples. Spend $0.0986 (the failed call counted at worst case).
