# Voice doc-chat

A small voice assistant that runs on the laptop: you speak (or type), it finds the answer in a folder of documents or asks Qwen, and the answer is spoken back. Its purpose is to **feel and measure the latency of the voice loop** (speech in, text, answer, speech out), not to be a smart assistant.

```
mic ──▶ lan_speech /stt (Kroko) ──▶ question ──▶ retrieval and/or Qwen ──▶ lan_speech /tts (Supertonic) ──▶ speakers
```

## The improved loop (Parakeet, names from your documents, clarifying questions, history)

The same loop with the pieces built since the first version wired in. Nothing below changes the defaults of the plain commands further down; it adds options.

```bash
uv run python scripts/fetch_laptop_models.py        # once; also fetches Parakeet (~660 MB)
uv run python scripts/make_entities.py corpus/harry-potter -o config/entities-docs.json   # or use the committed file
# 1. the speech server: Kroko streams, Parakeet transcribes each finished turn, biased toward the names
uv run --group laptop python -m lan_speech --stt-model parakeet --insecure-loopback \
    --entities config/entities-docs.json
# 2. the chat client
uv run --group laptop python -W ignore -m doc_chat --mic --play --docs corpus/harry-potter --llm qwen --stream
```

What is different from the first version:

| | First version | Improved loop |
|---|---|---|
| End of turn | waits for the server's own detector (1.5-2.5 s with Kroko) | this client ends the turn after `--vad-hold` seconds of quiet (default 0.6), measured against the room's noise; the server's detector is only a fallback |
| Recogniser | Kroko (about 22% word errors on names) | Parakeet with the document's names as hotwords (`config/entities-docs.json`, 125 names pulled from the corpus by `scripts/make_entities.py`): "Dumbledore", "Order of the Phoenix" |
| A name heard almost right | passes through as heard | repaired ("Hermoin" -> "Hermione"), and the screen shows `(recognised as: ...)` |
| A name heard doubtfully | passes through | the assistant asks "Did you mean Dumbledore?" and waits: "yes" answers the corrected question, "no" asks you to say it again, anything else is taken as a new question |
| Follow-ups ("and her sister?") | each question stands alone | the last `--history` (4) question/answer pairs go to the model, and a short follow-up is searched together with the previous question |
| Timing line | `stt` is null in mic mode | `stt 910 ms (pause 600 + server 310)` measured from your last sound; `answer starts ... after you stopped` counts from the same moment |

New options: `--vad-hold SECONDS` (0 leaves the decision to the server), `--history N` (0 = no memory), `--slot doc?` (sent to the server; the trailing `?` means names are repaired when heard but a question without one is never questioned), `--risk low|high`. The log (`out/doc_chat/turns.jsonl`) gains `pause_ms`, `stt_server_ms`, `repair_rung`, and a row per clarifying question (`clarification`).

The name list is read when the speech server starts, so restart it after changing `--entities`. Start it without `--entities` and the plain Parakeet engine still works (hotwords from `config/entities.json`, none of them about the documents).

Checked end to end on synthetic questions through the real speech server (Parakeet with the document names, name repair, streaming speech) with a scripted stand-in for Qwen: the answer started 520-600 ms after the last word in WAV mode (the client ends the turn at once there; live use adds the `--vad-hold` pause), "Is Hermoin a witch?" was repaired to Hermione, and the history grew to the 4-pair cap. **Not verified:** the real Qwen: from this machine `python -m doc_chat.qwen` timed out (the class server may need the VPN), so the model path was exercised with the stand-in only.

Still half-duplex: the microphone is closed while the answer plays, so there is no barge-in. The speculative start of ticket 12 (run the model on the early transcript, hold its audio, release it if the final matches) is not built; `docs/STT-HOSTING.md` compares it with the alternatives.

## Quick start

Two terminals, both from the repo root.

```bash
# 1. the speech server (loads Kroko + Supertonic; wait for /healthz)
uv run --group laptop python -m lan_speech --insecure-loopback --endpoint-silence 0.7
curl http://127.0.0.1:8765/healthz        # {"ok": true, "stt": "sherpa-kroko", "tts": "supertonic-3"}

# 2. the chat client
uv run --group laptop python -W ignore -m doc_chat --mic --play --docs corpus/harry-potter
```

Speak a question and pause. The terminal shows what it heard, the answer, and a timing line, and the answer is spoken. Say "quit" or press Ctrl-C to stop.

Models are fetched with `uv run python scripts/fetch_laptop_models.py --server-only` (it verifies SHA-256 and prints licenses). The server refuses to start, naming the file, if one is missing or corrupt.

## Inputs

| Flag | What it does |
|---|---|
| `--mic` | Hands-free conversation loop from the default microphone. Half-duplex: the mic is closed while the answer plays (no echo, but no barge-in). |
| `--text "question"` | One typed question; skips speech recognition. |
| `--wav a.wav b.wav` | Replays recordings as if spoken, paced in real time. |
| `--play` | Play the answer through the speakers (playback runs in the background, so the next sentence can already be synthesizing). |
| `--out DIR` | Also save each answer as `turnNN.wav`. |

## Where answers come from

| Mode | Command part | What happens |
|---|---|---|
| Passage (default) | `--docs DIR` | BM25 keyword search over the `.md`/`.txt` files in `DIR`; the best-matching passage, trimmed to `--max-words` (40), is the answer. No model involved. |
| Qwen over documents | `--docs DIR --llm qwen` | The top 2 passages go to Qwen as context; Qwen writes a short spoken answer. |
| Pure Qwen chat | `--llm qwen --no-docs` | No retrieval; the question goes straight to Qwen. |

Every question is a fresh one-shot call: no conversation history is kept.

## Asking "anything to add?" when you trail off (`--ask-to-add`)

When what the speech server heard ends like an unfinished sentence ("tell me about the Order of
the"), the agent says a short prerecorded line ("Got it. Anything to add?"), listens for
`--add-wait` seconds (3), and joins your answer to the first part. The two parts' audio is sent to
the speech server as one turn and decoded once (4.7% word error in the simulation, against 13.5% for
joining the two texts). A "no", "that's all" or silence keeps the turn as it was. Turns the server
wants to clarify (a doubtful name) take the "Did you mean?" path instead.

```bash
uv run python scripts/fetch_turn_model.py          # 176 MB text model, once
uv run --group laptop python -W ignore -m doc_chat --mic --play --docs corpus/harry-potter \
    --llm gemini --stream --ask-to-add
```

`--add-threshold` (default 0.3) is how unfinished the words must look: lower asks less often.
In the simulation 0.3 asked 2% of finished turns and caught about 60% of trailing-off pauses.
Each ask is logged (`asked_to_add`, `unfinished_score`, `added`). Mic only; the early-send half of
the policy in `docs/SMART-TURN.md` (start deciding at a 200 ms pause) needs the speech server to let a
turn be resumed and is not built.

Tried end to end with the real server, Gemini and a scripted microphone
(`scripts/e2e_ask_to_add.py`): "Tell me about the Order of the" (score 0.11) -> prompt -> "Phoenix."
-> joined and answered; silence and "no" kept the original. Limits seen: Parakeet sometimes
completes a hanging phrase itself ("...what Dumbledore said to" came back as "...said to me.") and
then nothing is asked; and the answer to the prompt is a second recognition pass, so the stitched
turn took 1.1 s to transcribe (0.6 s of it server time).

## Gemini as the model

`--llm gemini` uses Google's OpenAI-compatible endpoint with `gemini-3.1-flash-lite` (change it
with `--llm-model` or env `GEMINI_MODEL`). The key is read from
`~/.secrets/google-ai-studio/api_key.txt` or `api-key.txt` (override the folder with
`GOOGLE_AI_SECRETS_DIR`), then env `GEMINI_API_KEY`; it is never printed. Check it with
`python -m doc_chat.gemini` (prints only OK or the failure class). Everything else (history,
`--stream`, `--no-docs`) works as with Qwen.

Measured from this machine (6 streamed replies of two short sentences): first token about 0.9 s
(0.74-1.27 s), whole reply about 1.0 s; the thinking setting made no difference. That first token
is the largest single item in the latency budget.

## Testing Qwen

**1. Credentials.** The key is read from `~/.secrets/onit/api_key.txt` and the server address from `~/.secrets/onit/host_address.txt` (falling back to env `ONIT_API_KEY` / `ONIT_BASE_URL`, then opencode's `upd-ai` provider; a bare `host:port` becomes `http://host:port/v1`). The model id comes from `--llm-model`, env `ONIT_MODEL` or opencode's config. `ONIT_SECRETS_DIR` points at another folder. Nothing here ever prints a key or an address.

**2. Connectivity check** (needs no speech server):

```bash
uv run --group laptop python -m doc_chat.qwen
```

It sends one tiny request and prints `qwen check ok (N ms)` or the reason it failed:

| Message | Meaning |
|---|---|
| `rejected the API key (HTTP 401)` | the key in `api_key.txt` is not accepted |
| `could not connect to the server address` | wrong address, or not reachable from this network (VPN?) |
| `the server did not answer in time` | slow or overloaded server; raise `--llm-timeout` |
| `path or model not found (HTTP 404)` | wrong model id (try `--llm-model`) or the address already points past `/v1` |
| `qwen backend not configured` | a file or value is missing |

**3. One typed question** (needs the speech server for the spoken answer):

```bash
uv run --group laptop python -W ignore -m doc_chat --text "Why is the sky blue?" --llm qwen --no-docs --play
```

**4. Talk to it**, first waiting for the whole reply, then streaming it, and compare the timing lines:

```bash
uv run --group laptop python -W ignore -m doc_chat --mic --play --llm qwen --no-docs
uv run --group laptop python -W ignore -m doc_chat --mic --play --llm qwen --no-docs --stream
```

With `--stream`, Qwen's reply is cut into sentences as it arrives and each sentence is spoken as soon as it is complete, so the first words come back while the rest is still being written. Without it, nothing is spoken until the whole reply has arrived. A sentence is released once the next one starts, so "J.K. Rowling" or "3.14" are never split in the middle.

### Options

| Flag | Default | Notes |
|---|---|---|
| `--llm qwen` | off | needed for everything below |
| `--no-docs` | off | skip retrieval |
| `--stream` | off | speak sentence by sentence |
| `--llm-model ID` | from config | model id |
| `--llm-timeout S` | 30 | seconds before giving up |
| `--llm-max-tokens N` | 160 | reply length cap (also bounded by `--max-words`) |
| `--llm-temperature T` | 0.2 | |
| `--max-words N` | 40 | spoken answer is cut at a sentence end below this |
| `--system-prompt FILE` | built in | replaces the system prompt; with documents, `{context}` marks where the passages go (appended if missing) |

The built-in prompt asks for at most two short, plain spoken sentences with no markdown, lists or emoji. Thinking mode is switched off (`enable_thinking: false`), and any `<think>` text that still arrives is dropped and never spoken.

If Qwen fails, the turn does not crash: with documents the retrieved passage is spoken instead; without them a short apology is. Either way the screen shows `[llm failed - <reason>]` and the log records `llm_error`.

## Reading the timing line

```
you:  Why is the sky blue?
bot:  Sunlight scatters off air, and blue light scatters the most.
  [stt 41 ms | retrieval 0 ms | llm first token 620 ms | llm 1180 ms | tts first audio 330 ms | answer starts 980 ms after you stopped]
```

| Field | Meaning |
|---|---|
| `stt` | time from your last sound to the final transcript (WAV mode; null in mic mode) |
| `retrieval` | document search (shown only when above 0) |
| `llm first token` | time until Qwen's first word arrived (`--stream` only) |
| `llm` | total time Qwen took (with `--stream`, until its last sentence was handed to speech) |
| `tts first audio` | from sending the first sentence to the first audio coming back |
| `answer starts … after you stopped` | the delay you actually feel. In mic mode it is measured from the moment the end-of-turn transcript arrived, so the server's trailing-silence wait (`--endpoint-silence`) comes on top. |

Each turn is also appended to `out/doc_chat/turns.jsonl` (`--log` to change) with these fields: `turn, question, answer_words, stt_final_ms, retrieval_ms, llm_ms, llm_first_token_ms, llm_error, tts_first_audio_ms, tts_total_ms, speech_end_to_first_audio_ms, source`. A latency summary (p50/p95) prints on exit.

## Speech recognition

The server uses **Kroko** (`sherpa-onnx` streaming Zipformer, English) by default. On 24 sentences of real microphone speech it made about half as many mistakes as the previous LibriSpeech Zipformer (word error rate 9.8% against 15.5%), runs at 0.04x real time (about 45 ms after you stop talking), slows only 1.2x while speech synthesis runs, and writes capitals and punctuation. Details and the other candidates are in `docs/STT-COMPARE.md`. The old model is still available with `--stt-model zipformer`.

- **End of turn:** the server ends your turn after `--endpoint-silence` seconds of quiet (default 1.2). 0.7 feels snappier; too low cuts you off mid-sentence.
- **Silence matters:** Kroko drops a word that starts exactly at sample 0 or ends exactly at the last sample. A live microphone always has quiet around speech; replayed WAVs get a 0.5 s lead-in and the server adds 1.5 s of trailing silence when asked to finalize.
- **License:** Kroko's community weights are CC-BY-SA per the model card, but the Hugging Face repo's `LICENSE` file is empty and its license field says "other". Fine for local class use; the model files are not redistributed.

## How speech is chunked and paced

The server speaks an answer chunk by chunk, in order. A chunk ends at `, ; : . ! ?` followed by a space or the end of the text, optionally with a closing quote or bracket (so `."` ends a sentence). A period after initials or an abbreviation (`J.K.`, `Dr.`, `Mr.`, `e.g.`) is not a boundary, so "J.K. Rowling" is read as one. A first chunk over 6 words is split after word 6 so audio starts sooner.

Supertonic pads every chunk with 0.3–0.6 s of silence, which made gaps of about 1 s. The server trims that and inserts its own pause after each chunk, shortest for commas:

| After | Default pause | Flag |
|---|---|---|
| `,` | 0.12 s | `--comma-pause` |
| `;` `:` | midpoint (0.26 s) | derived |
| `.` `!` `?` | 0.40 s | `--sentence-pause` |

Measured gaps include about 80 ms of edge margin on top. Speech becomes audible about 0.5 s after the request instead of about 0.9 s.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `speech server unavailable` | the server is not running or not finished loading; check `curl http://127.0.0.1:8765/healthz` |
| `install the laptop group for --mic/--play` | run through `uv run --group laptop` |
| `--no-docs and --stream need --llm qwen` | add `--llm qwen` |
| Wrong words | check the room and mic level with `scripts/stt_compare.py record`; mic distance and gain matter more than the model |
| Long pause before the answer starts | read the timing line: a large `llm` or `llm first token` means Qwen is the slow part; try `--stream` |

## Earlier measurement (previous STT model)

Measured with the previous Zipformer and before the chunk silence was trimmed; kept for comparison only. 40 synthetic turns, AC power with `scripts/power_mode.sh max`, Supertonic 4 steps and 2 threads, over loopback. "Answer starts" counts when the first audio bytes arrived, which was about 0.4 s before speech was actually audible.

| Timing field | p50 (ms) | p95 (ms) |
|---|---|---|
| `stt_final_ms` | 28.6 | 37.4 |
| `retrieval_ms` | 0.4 | 0.5 |
| `tts_first_audio_ms` | 310.9 | 416.6 |
| `speech_end_to_first_audio_ms` | 343.7 | 445.4 |

The input was synthetic speech rendered by Kokoro, so it says little about recognition accuracy.
