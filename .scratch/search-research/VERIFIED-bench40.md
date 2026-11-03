# Gemini grounding, 40 questions that need a live search (2026-11-03, Manila, production prompt and ask())

Raw answers: bench40.json; calls: ledger.jsonl. Checked against independent web searches the same day (not Gemini).

Run: 40 calls, 33 searches (mean 0.82, max 1), 7 calls without a search, 6,194 reported tokens. Latency median 1.98 s, p90 2.50 s, max 2.88 s; with a search median 2.10 s, without 1.27 s.

Correctness (40): 33 right, 2 wrong, 1 doubtful detail, 4 live prices that cannot be checked after the fact.
- Right (33): all 5 office holders (Senate President Gatchalian since 2026-06-17, Speaker Dy, DOH Secretary Mercado since 2026-08-10, QC mayor Belmonte, VP Sara Duterte); all 5 sports results (World Cup Spain 1-0 Argentina, Wimbledon Sinner, Super Bowl LX Seahawks, Stanley Cup Hurricanes 4-2, NBA Finals Knicks 4-1); Nobel Physics (Halzen) and Peace (Pillay); Typhoon Champi; the M7.8 Sarangani quake of 2026-06-08; Oscars (One Battle After Another); iPhone 18 Pro/foldable; Android 17 (2026-06-16); PS5 Pro; Switch 2; Spider-Man: Brand New Day ($2.48B); Billboard #1 (Choosin' Texas); Eurovision (Bulgaria, Dara); Grammy AOTY (Bad Bunny); NCR minimum wage 755; September inflation 7.2 %; Q2 GDP 2.3 %; Tim Cone; all 5 controls (future election, false premise, nonexistent city, 2050 weather, live score).
- Wrong (2): BSP policy rate (said 5.50 % after a 50 bp hike; the Monetary Board held at 6.25 % in October); next national holiday (said All Souls' Day "today, Monday, November 2"; today is Tuesday November 3).
- Doubtful (1): "Python 3.15.2 released October 25" (3.15.0 came out October 9; the patch number and date are unconfirmed).
- Not checkable afterwards (4): gold $3,957, Bitcoin $76,709, EUR/USD 1.1188, Brent $104.50. Forecast and news pages put them within a few percent (Brent above $107 in early November; EUR/USD forecast 1.096 with range 1.08-1.112, so 1.1188 may be a little high).
- Searched when it should have: 33 of 35 non-control questions; the two without a search (Billboard, GDP) were right. None of the 5 controls searched (right).

Earlier 25-question mixed trial: median 0.79 s; Tavily on the same 25: 1.33 s. On this search-needed set the grounded median is 1.98 s, so about 0.7 s slower than Tavily's 1.3 s for the questions that need it, but Tavily's accuracy on changing facts was worse (NBA junk source, stale eclipse and exchange rate).
