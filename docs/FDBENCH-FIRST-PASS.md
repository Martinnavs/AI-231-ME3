# fdbench first pass (2026-11-07): how it was run and what it measured

Class harness: https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking, cloned to `fd-benchmark/` (git-ignored,
own `.venv`, datasets from its GitHub Release). Our assistant is not Pipecat, so it runs in **virtual-cable mode**
with `mark()` calls from the laptop client (`src/doc_chat/fdbench_marks.py`, vendored unchanged). Stack: laptop router
and speech, the agent harness on the Pi (boot services `pi-agent`, `pi-speaker`), Pi tuned (`make pi-tune`).
What is NOT in the path: the Pi speaker hop and echo cancellation (the client plays to a null sink).

## Reproduce
1. `bash fd-benchmark/scripts/setup_virtual_audio.sh` (sinks `fdb_to_bot`, `fdb_from_bot`; redo after reboot).
2. Router and speech up (`HARNESS=pi make up`; if it fails with port 8780 in use, the Pi services are already running
   as boot services, so only the laptop part is needed).
3. `scripts/stack/fdbench_client.sh &` (client with `FDBENCH_MARKS=udp://127.0.0.1:8792`). Then move its streams:
   `pactl move-source-output <id> fdb_to_bot.monitor` and `pactl set-default-sink fdb_from_bot` (PULSE_* variables are
   ignored by the ALSA path). Restore the default sink afterwards. Never `pkill -f "doc_chat --mic"` (kills your own
   shell); use `pgrep -f "[d]oc_chat --mic" | xargs kill`.
4. Stage test: `cd fd-benchmark && .venv/bin/python -m fdbench run --connect cables --to-bot fdb_to_bot --from-bot fdb_from_bot --student me3 --rounds 0 --phrases 10 --runs 3 --judge-provider none --no-asr --yes --no-live --recordings k --models "ASR=Parakeet (lan_speech),LLM=router 350M kd-web4 + Gemini,TTS=Kitten,VAD=Silero"` (about 9 min).
5. Groups: same without `--phrases`, with `--rounds 2 --phrases 0 --seed 922546` (about 13 min). Judge afterwards, from saved audio:
   `OPENROUTER_API_KEY="$(tr -d '\n' < ~/.secrets/openrouter/api-key.txt)" .venv/bin/python -m fdbench score RUN_DIR --judge-provider openrouter --judge-model anthropic/claude-haiku-4.5`
   (never print the key; judge for 2 rounds cost under $0.03).
fdbench picks the stage-test phrases at random per run unless `--phrases-dir` is given; pin it for before/after tests.

## Results
Stage latency, run `2026-11-07_15-00-16` (10 phrases x 3, tuned Pi, AC, Wi-Fi laptop):

| Stage | Median | p90 | p95 | Mean | n |
|---|---|---|---|---|---|
| E2E (VAD end to first sound) | 1.48 s | 1.87 s | 2.15 s | 1.32 s | 30 |
| same, from true end of speech (adds 0.60 s wait) | 2.10 s | 2.60 s | 2.71 s | 1.95 s | 30 |
| ASR final | 0.36 s | 0.40 s | 0.41 s | 0.35 s | 30 |
| LLM first token | 0.91 s | 1.21 s | 1.51 s | 0.79 s | 30 |
| TTS first audio | 0.21 s | 0.27 s | 0.27 s | 0.21 s | 23 |
| WER | 0.0 % (median) | | | 9.9 % mean | 30 |

An earlier untuned run with marks off (different random phrases) had audio-measured E2E median 2.26 s, p90 2.73 s, p95 3.13 s.

Full-Duplex-Bench, run `2026-11-07_15-33-39` (12 rounds, seed 922546, n = 12 per group, 24 false-turn-taking trials; judge Haiku 4.5; 168 clips, about 80 min, judge about $0.20):

| Group | Result |
|---|---|
| v1 pause handling TOR, synthetic / Candor (lower better) | 17 % / 17 % (2/12 each) |
| v1 turn-taking TOR, latency | 100 % (12/12), 2.95 s median, p95 3.36 s |
| v1 interruption TOR, judge rating, latency | 100 % (12/12), 4.0/5, 2.61 s median |
| Barge-in: playback stopped (target < 0.8 s) | mean 0.70 s, p95 0.90 s (n = 16); 1 interruption she talked through |
| v1 interruption t_stop | median 0.80 s but p95 8.7 s (one very late stop) |
| v1.5 interruption Respond / Resume | 100 % / 0 %, t_stop 0.72 s, t_resp 2.55 s |
| v1.5 backchannel Resume / Respond (Resume better) | 83 % / 0 %, t_resp 1.28 s |
| v1.5 talking to others Resume / Respond (Resume better) | 8 % / 83 %: answers speech not meant for it |
| v1.5 background speech Resume / Respond | 67 % / 17 %, 1 unknown |
| False turn-taking, answers when it should stay silent (lower better) | **75 % (18/24)**: side talk 6/6, TV/radio 6/6, background speech 6/6, noise 0/6 |

The 2-round smoke test (run `15-16-38`) had said 50 % false turn-taking and 50 % pause take-overs; the 12-round rates are the ones to quote.

## Gaps to close
- Context size is "not reported": `llm_request` mark carries a message count, not tokens.
- TTS first audio n = 23 of 30 (fast-path and filler turns skip the TTS-start mark).
- False turn-taking 75 % is the biggest weakness (no addressee gate: it answers side talk, TV and background speech; only noise is ignored). Talking-to-others Respond 83 % is the same cause. Pause take-overs 17 % are acceptable.
- One late stop in v1 interruption (t_stop p95 8.7 s) and one talk-through: worth looking at in the recordings.
- 12 rounds are done; for a fair before/after keep `--seed 922546`. Compare with Donna (me3-the-donna, E2E 1.02 s median, tools off) and Watson (me3-full-duplex, 2.57 s) only in the same mode: ours ran with tools on.
