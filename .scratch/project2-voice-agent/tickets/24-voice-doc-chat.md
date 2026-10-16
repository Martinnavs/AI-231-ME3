# 24: Voice doc-chat — LangChain retrieval "chat" through the laptop speech server, to measure STT/TTS latency

**Repo:** the agy worktree (branch `agy/laptop-speech`)
**Blocked by:** 21 Phase 2 committed on this branch. That is the producer: import the real `src/lan_speech/protocol.py` and test against the real server with fake engines, never against the ticket's prose (Operating Principle 11).
**Status:** done

## Why
The goal is to feel and measure the voice loop's latency (speech in → text → answer → speech out) on the laptop speech server, not to build a smart assistant. A generic retrieval chat over a folder of documents gives realistic, variable-length answers without any LLM call. Answers are the retrieved text itself, returned as primitive LangChain outputs (`Document` page content passed through a `Runnable`). An optional Qwen path is scaffolded for later but never called in this ticket.

## Files
- `src/doc_chat/__init__.py`, `src/doc_chat/corpus.py` (load + split docs), `src/doc_chat/chain.py` (retriever → answer `Runnable`), `src/doc_chat/speech.py` (WebSocket client for `/stt` and `/tts`, built on `lan_speech.protocol`), `src/doc_chat/__main__.py` (CLI loop + timing)
- `src/doc_chat/qwen.py` (scaffold only, see Contracts)
- `pyproject.toml` (`laptop` group additions only; add `doc_chat` to mypy packages and hatch wheel packages; mypy overrides for untyped imports)
- `docs/DOC-CHAT.md` (how to run, the timing fields, latency results table)
- `tests/doc_chat/test_corpus.py`, `tests/doc_chat/test_chain.py`, `tests/doc_chat/test_session.py`, `tests/doc_chat/test_qwen_live.py` (marked `live`)

## Action
- **Corpus:** `--docs DIR` (default `docs/`) loads every `*.md`/`*.txt` file and splits it with `RecursiveCharacterTextSplitter` (about 500 chars, 50 overlap). Each chunk keeps `source` metadata.
- **Retrieval:** `BM25Retriever.from_documents(chunks, k=2)`. No embeddings, no model.
- **Answer (`--llm none`, the default):** a `Runnable` takes the top chunk's text, strips Markdown syntax (headings, `|` tables, code fences, link URLs), and keeps whole sentences up to `--max-words` (default 40). That text is the answer. With no hit, the answer is a fixed "I couldn't find that in the documents."
- **Voice turn:**
  1. Stream audio to `/stt`. Inputs: `--wav` files, paced at real time in 100 ms chunks; or `--mic` via `sounddevice` at 16 kHz mono, where the end of turn comes from the server's `end_of_turn=true`.
  2. Send `finalize` after the last sample of a WAV.
  3. Take the final transcript.
  4. Run retrieval and the answer `Runnable`.
  5. Send `speak` to `/tts`.
  6. Receive audio chunks, and play them (`--play`, `sounddevice`) or write them to `--out DIR/turnNN.wav`.
  `--text "question"` skips STT for a TTS-only check.
- **Barge-in (minimal):** in `--mic --play` mode, a new final transcript while TTS is still sending makes the client send `cancel` for the current context and stop playback. Measure `cancel_to_silence_ms`.
- **Timing (the point of this ticket):** each turn appends one JSON line to `--log out/doc_chat/turns.jsonl`, using `time.perf_counter()` on the client:
  `{"turn":int,"question":str,"answer_words":int,"stt_final_ms":float,"retrieval_ms":float,"tts_first_audio_ms":float,"tts_total_ms":float,"speech_end_to_first_audio_ms":float,"cancel_to_silence_ms":float|null,"source":str|null}`
  - `stt_final_ms` runs from the last audio sample sent to the final transcript.
  - `speech_end_to_first_audio_ms` runs from that same instant to the first TTS byte received: the latency a user hears.
  At exit the CLI prints p50/p95 of each timing field.
- **Latency run:** with the real engines on AC (`scripts/power_mode.sh max`, then `balanced` after), start the server on 127.0.0.1 with `--insecure-loopback`. Run `python -m doc_chat --wav out/laptop_bench/renders/*.wav --docs docs/ --out out/doc_chat/`. If the renders are missing, generate them with `scripts/laptop_speech_bench.py`'s render step. Put the p50/p95 table in `docs/DOC-CHAT.md`, and say in the doc that the input is synthetic TTS audio.
- **Qwen scaffold (`--llm qwen`, never run by the agent):** `qwen.py` builds a `ChatOpenAI` from `langchain-openai`. At runtime it reads `~/.config/opencode/opencode.json` → `provider["upd-ai"].options.baseURL` and the first model id under `provider["upd-ai"].models`. The API key comes **only** from env `ONIT_API_KEY`, the variable that config's `{env:ONIT_API_KEY}` points at. The answer chain becomes retrieval → prompt ("answer in ≤ 2 short sentences using only this context") → model → `StrOutputParser`. If the config or the env var is missing, the CLI exits 2 with `qwen backend not configured`, without echoing any value. Unit tests inject a `FakeListChatModel`; the only real call is in the `live` test, left for the human.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| server unreachable / 401 | exit 2 with `speech server unavailable` or `speech server rejected token`; the token is never printed |
| server sends `error` mid-turn | log the turn with that stage's timing `null` plus an `"error"` field; continue with the next input |
| empty transcript | skip retrieval and TTS; log the turn with `answer_words=0` |
| no retrieval hit | the fixed "couldn't find" answer, still spoken |
| answer longer than `--max-words` | cut at a sentence boundary before the limit; if a single sentence is too long, at a word boundary |
| `--docs` empty or missing | exit 2 `no documents found in <dir>` |
| `--llm qwen` without config/env | exit 2 `qwen backend not configured`; nothing about the key's value |
| Ctrl-C mid-TTS | send `cancel`, close both sockets, flush the jsonl; no traceback |
| `sounddevice` not installed and `--mic`/`--play` used | exit 2 `install the laptop group for --mic/--play` |

### C — Contracts
```text
python -m doc_chat [--server ws://127.0.0.1:8765] [--docs DIR] (--wav F... | --mic | --text Q)
                   [--play] [--out DIR] [--log FILE] [--max-words 40] [--voice F1] [--llm none|qwen]
token: env LAN_SPEECH_TOKEN (same as the server); omitted only when the server runs --insecure-loopback

doc_chat.chain.build_answer_chain(retriever, *, max_words: int = 40, llm: BaseChatModel | None = None)
    -> Runnable[str, Answer]
@dataclass(frozen=True) class Answer: text: str; source: str | None; retrieval_ms: float
```
The wire protocol comes from `lan_speech.protocol` and is never redefined here.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `langchain-core`, `langchain-community` (`BM25Retriever`), `langchain-text-splitters`, `rank-bm25`, `langchain-openai` and `sounddevice` install via `uv add --group laptop`, and the default group is unchanged | `uv run --group laptop python -c "from langchain_community.retrievers import BM25Retriever; import langchain_openai, sounddevice"`, then `uv sync && uv pip list \| grep -i langchain` → nothing |
| A2 | `sounddevice` finds a PortAudio library on this laptop | `uv run --group laptop python -c "import sounddevice as sd; print(sd.query_devices())"`. If PortAudio is missing, **do not install system packages**: make `--mic/--play` fail per Edges and record it |
| A3 | LangChain import paths match the installed versions (they drift) | check with `uv pip show` plus a real import before writing code; don't code from memory |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_corpus_split_and_sources`: chunks carry `source`; an empty dir hits the exit-2 path | `uv run --group laptop pytest -q tests/doc_chat/test_corpus.py` |
| P2 | `test_answer_chain_bm25`: a question about a fixture doc returns that doc's sentence, Markdown is stripped, `max_words` is honored, and no hit gives the fixed answer | `… tests/doc_chat/test_chain.py` |
| P3 | `test_answer_chain_with_fake_llm`: `FakeListChatModel` output flows through `StrOutputParser`; no network | same |
| P4 | `test_session_end_to_end`: runs **`doc_chat.__main__.main([...])`** (the real entry point) against the real `lan_speech` server started in-process with **fake engines** on 127.0.0.1. Two WAV turns produce two jsonl lines with every timing field present and non-negative, plus an output WAV per turn | `… tests/doc_chat/test_session.py` |
| P5 | `test_cancel_on_new_turn`: with fake engines, a second transcript during TTS sends `cancel`, no stale audio of the cancelled context is written afterwards, and `cancel_to_silence_ms` is logged | same |
| P6 | `test_qwen_not_configured`: with no env var and a temp HOME without the config, exit 2 and the message holds no value | same file or `test_chain.py` |
| P7 | `test_qwen_live` (`live`, human-run): one real question through Qwen | `VOICE_AGENT_LIVE=1 uv run --group laptop pytest -q -m live tests/doc_chat/test_qwen_live.py` — leave for the human |
| P8 | latency table (p50/p95 of every timing field, 40 synthetic turns, AC + max) present in `docs/DOC-CHAT.md` | `grep -n "speech_end_to_first_audio" docs/DOC-CHAT.md` |
| P9 | Pi install unchanged and gate suite green without the laptop group (the doc_chat tests `importorskip`) | `uv sync && make check && uv run pytest -q` |

## Dependencies/license
`langchain-core`, `langchain-community`, `langchain-text-splitters`, `langchain-openai` (MIT), `rank-bm25` (Apache-2.0), `sounddevice` (MIT). All go in the `laptop` group only.

## Acceptance Criteria
- [x] P1–P6, P8, P9 green; P7 written and left for the human
- [x] `docs/DOC-CHAT.md` has the run instructions and the latency table, with the synthetic-audio caveat
- [x] No LLM/API call was made; no secret value appears in code, logs, docs or test output
- [x] Pi install unchanged

## Non-Goals
- No real LLM answers in this ticket (Qwen is a scaffold), no embeddings or vector DB, no conversation memory
- No change to `lan_speech` beyond what a bug fix in ticket 21's own scope would need. If one is needed, stop and report it instead of editing 21's files here
- No Pi deployment, no `audio_engine` backend (that is ticket 22)

## Security-sensitive
no (client only; it reads a token from env and must not print it).

## Shared Context
- `.scratch/project2-voice-agent/AGY-GUARDRAILS.md` (overrides), `.scratch/project2-voice-agent/RUNNER.md`, `docs/LAPTOP-SPEECH.md` (protocol section), `src/lan_speech/protocol.py`

## Execution Log

### 2026-10-16 developer: DONE
- Assumptions verified: A1 `uv run --group laptop python -c "from langchain_community.retrievers import BM25Retriever; import langchain_openai, sounddevice"` → ok; `uv sync && uv pip list | grep -i langchain` → nothing in default env; A2 `uv run --group laptop python -c "import sounddevice as sd; print(sd.query_devices())"` → PortAudio found on laptop (ALSA/JACK devices present); A3 LangChain import paths checked (`RecursiveCharacterTextSplitter`, `Document`, `Runnable`, `BM25Retriever`, `ChatOpenAI`, `StrOutputParser`).
- Proof: P1 ✅ (`tests/doc_chat/test_corpus.py`) P2 ✅ P3 ✅ P6 ✅ (`tests/doc_chat/test_chain.py`) P4 ✅ P5 ✅ (`tests/doc_chat/test_session.py`) P7 ⏭️ (marked `live`, left for human in `tests/doc_chat/test_qwen_live.py`) P8 ✅ (40-turn latency table in `docs/DOC-CHAT.md`) P9 ✅ (Pi default env passes `uv sync && make check && uv run pytest -q` → 244 passed, 13 skipped, 2 xfailed) (commands: `uv run --group laptop pytest -q tests/doc_chat` → 10 passed, 1 skipped)
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (260 passed, 9 skipped, 2 xfailed)
- Files: `src/doc_chat/__init__.py`, `src/doc_chat/corpus.py`, `src/doc_chat/chain.py`, `src/doc_chat/speech.py`, `src/doc_chat/__main__.py`, `src/doc_chat/qwen.py`, `docs/DOC-CHAT.md`, `tests/doc_chat/test_corpus.py`, `tests/doc_chat/test_chain.py`, `tests/doc_chat/test_session.py`, `tests/doc_chat/test_qwen_live.py`, `pyproject.toml`, `uv.lock`
- Deviations: none
- Commit: 96e2ea6
- Notes for reviewer: Latency measured over 40 synthetic turns against in-process LAN speech server on AC power with max performance profile. User-perceived latency (`speech_end_to_first_audio_ms`) achieved p50 = 343.7 ms and p95 = 445.4 ms.
