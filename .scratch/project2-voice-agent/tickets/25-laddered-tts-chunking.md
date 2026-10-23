# 25: Laddered TTS chunking — small first chunk, growing chunks, in the reply streamer

**Repo:** /mnt/School/PhD/AI231/AI-231-ME3-agy
**Blocked by:** None (built in `doc_chat` first; ported by ticket 11's `EngineTTSProcessor`)
**Status:** ready-for-agent

## Why
Measured 2026-10-23 on 11 warm Gemini replies: final transcript to first audio p50 1.46 s = Gemini first token 1.03 s + wait for the first sentence to end 0.06 s + TTS first audio 0.39 s. TTS first audio on the live server by chunk size: 3 words 250 ms, 4 words 283 ms, 6 words 383 ms, 8 or more about 410 ms (flat to 25 words). TTS runs at RTF about 0.2 with about 250 ms fixed cost per call. Expected gain 0.15-0.2 s of about 2.5 s; not the main lever, but small and low risk.

## Files
- `src/doc_chat/chain.py` (`SentenceStream`: new `ChunkLadder`), `src/doc_chat/__main__.py` (flag `--tts-ladder`, default off until measured)
- `tests/doc_chat/test_chain.py` (ladder table), `docs/PI-REMOTE-SPEAKER.md` (results)

## Action
A chunker over the LLM token stream. Rule: first chunk ends at the first `.?!` or at the first `,` once it has at least 4 words (hard cap 8 words); the second chunk targets about 10 words; later chunks are whole sentences. Never split inside a number, abbreviation, or quoted title. Keep `SentenceStream`'s release rule and its backpressure behaviour. Barge-in cancel granularity is one chunk.

## RECAP
### E — Edges
| Edge | Behavior |
|---|---|
| reply of fewer than 4 words | one chunk |
| LLM stalls mid-reply | the speaker may run dry; log `tts_gap_ms` per chunk boundary and report p95 |
| barge-in between chunks | queued chunks dropped; only the spoken prefix goes to history |
### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | table test of the rules above on token streams | `uv run pytest -q tests/doc_chat/test_chain.py` |
| P2 | live: 10 warm questions with and without the ladder; report first-audio p50/p95 and `tts_gap_ms` p95 (n stated) | `docs/PI-REMOTE-SPEAKER.md` |

## Acceptance Criteria
- [ ] Ladder on beats off by at least 0.1 s at p50, with no audible gap greater than 150 ms at p95, otherwise leave default off and say so

## Non-Goals
- Changing the server's own clause splitting (`split_speech_chunks`)

## Security-sensitive
no

## Execution Log
