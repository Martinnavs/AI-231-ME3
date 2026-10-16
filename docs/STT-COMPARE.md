# Comparing STT models on your own voice

`scripts/stt_compare.py` records your microphone and scores streaming STT candidates on it (word error rate, proper-noun recall, words hallucinated on silence/typing, delay after speech, real-time factor).

## Record (separate terminal; it waits for the keyboard)

```bash
uv run --group laptop python scripts/stt_compare.py record --script bench/stt_script.txt
```

1. Stay silent for 3 s (it measures your room's noise floor; above -40 dBFS is noisy).
2. Read each sentence as soon as it appears. It stops by itself after about a second of quiet and moves on. Each clip shows its length and level, with warnings for too quiet or clipping.
3. Four silence/typing clips at the end (Enter to start each) catch noise hallucinations; skip them with `--no-noise`.
4. Redo bad clips with `record --script bench/stt_script.txt --only 07 12`. `--manual` switches to Enter-to-start/Enter-to-stop, and `--device N` picks an input device (`python -m sounddevice` lists them).

Without `--script` it uses 10 Harry Potter questions and 10 everyday ones. Recordings stay in `bench/mic_turns/` (git-ignored).

## Score

```bash
uv run --group laptop python scripts/stt_compare.py run --only baseline kroko nemo-fc-480ms nemo-fc-80ms --fast
```

Drop `--fast` to pace audio at real time (about as long as the recordings, per model). `nemotron-160ms` is also available but runs at 1.2x real time on 2 threads, so it cannot keep up. Models live in `models/stt-candidates/` (downloaded from Hugging Face; not part of the server's model check yet).

Every clip is padded with 0.5 s of silence before and 1.5 s after, as in live use: Kroko and the NeMo streaming models drop words at the very edges of a tightly trimmed clip.

## Synthetic smoke test (clean Kokoro voices, 6 clips, 2 threads, battery)

| model | WER | delay after speech p50 | RTF |
|---|---|---|---|
| baseline (current) | 0.077 | 91 ms | 0.11 |
| kroko | 0.000 | 42 ms | 0.04 |
| nemo-fc-480ms | 0.026 | 120 ms | 0.13 |
| nemo-fc-80ms | 0.026 | 275 ms | 0.34 |
| nemotron-160ms | 0.000 | 996 ms | 1.22 |

Clean synthetic speech says little about accuracy; it shows speed. Real-voice results go here once recorded.
