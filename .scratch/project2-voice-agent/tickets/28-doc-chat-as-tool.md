# 28: doc_chat retrieval as an agent tool

**Repo:** /mnt/School/PhD/AI231/AI-231-ME3-agy
**Blocked by:** 10 (tool registry), 11 (pipeline); the retrieval code already exists in `src/doc_chat`
**Status:** blocked until 10 and 11

## Why
Human decision 2026-10-23: the voice pipeline is Pipecat-based and `doc_chat` becomes a tool inside it, so the current testing (McCarthy corpus, BM25, follow-up queries, pivots) runs through the real agent path instead of a separate loop.

## Files
- `src/mcp_servers/docs.py` (or a local tool module, follow ticket 09's convention), `tests/mcp_servers/test_docs.py`, `docs/MCP-SERVERS.md` (section), `docs/DOC-CHAT.md` (how it is used)

## Action
Expose `docs__search(query, top_k=3)` returning the top chunks with their file names, using `doc_chat.corpus` and BM25 as is (chunk size 1000, top-k 3 as validated). The agent's system prompt handles follow-ups ("and when did he...") by passing a self-contained query; the old heuristic `retrieval_query` stays as a fallback in the tool. `doc_chat/__main__.py` stays runnable as the standalone loop until the pipeline reaches parity on the same metrics (interrupts, latency, echo).

## RECAP
### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | tool returns the validated chunks for 5 fixed McCarthy questions (same hits as `doc_chat` today) | `uv run pytest -q tests/mcp_servers/test_docs.py` |
| P2 | live: same 10 questions through the pipeline vs `doc_chat`; report latency and answer correctness side by side | `docs/PI-REMOTE-SPEAKER.md` |

## Acceptance Criteria
- [ ] Pipeline parity table written; `doc_chat` loop retired only after it

## Non-Goals
- A vector store

## Security-sensitive
no (reads only the corpus root; reuse `_paths.resolve_inside`)

## Execution Log
