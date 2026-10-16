# Comparing STT models on your own voice

`scripts/stt_compare.py` records your microphone and scores streaming STT candidates on it (word error rate, proper-noun recall, words hallucinated on silence/typing, delay after speech, real-time factor).

## Decision: Kroko (2026-10-16)

The speech server now defaults to **Kroko** (`--stt-model kroko`); the previous LibriSpeech Zipformer stays available as `--stt-model zipformer`.

Why: on real microphone speech it halves the mistakes of the old model, it is the fastest candidate by a wide margin, it slows least when speech synthesis shares the CPU, and it writes capitals and punctuation. NeMo FastConformer 480 ms is slightly more accurate (8.0% against 9.8%, a difference of about 7 words in 387) but about 3x slower and slows more under load. Nemotron cannot keep up with live speech on this CPU.

### Real microphone speech (24 sentences, 387 words, 2 threads, battery)

Read from `bench/stt_script.txt`; hands-free recording; room noise floor about -40 dBFS, dominated by a 50 Hz hum.

| model | word error rate | proper nouns right | words on silence/typing clips | delay after speech p50 / p95 | real-time factor |
|---|---|---|---|---|---|
| baseline (old Zipformer) | 15.5% | 3/3 | 0 | 87 / 90 ms | 0.07 |
| **kroko** | **9.8%** | 3/3 | 0 | **35 / 68 ms** | **0.03** |
| nemo-fc-480ms | 8.0% | 3/3 | 0 | 117 / 120 ms | 0.09 |
| nemo-fc-80ms | 9.0% | 3/3 | 0 | 280 / 286 ms | 0.23 |
| nemotron-160ms | 8.3% | 3/3 | 0 | 1038 / 1072 ms | 0.85 (1.2 in an earlier synthetic run) |

UK/US spelling variants (cosy/cozy) are counted as equal. The paragraph has only three proper nouns (Elena, Arthur, Saturday), so that column says little.

### While speech synthesis is running (8 of the clips, paced in real time)

| model | delay alone p50 | delay with Supertonic running p50 | slowdown | accuracy |
|---|---|---|---|---|
| baseline | 91 ms | 124 ms | 1.36x | unchanged |
| **kroko** | 47 ms | 56 ms | **1.20x** | unchanged |
| nemo-fc-480ms | 114 ms | 161 ms | 1.41x | unchanged |

All three stay inside the 1.5x limit set in ticket 21.

### Findings along the way

- **Not the microphone level:** speech sat at about -29 dB with peaks of 7,000–14,000.
- **The noise is hum, not hiss:** 82% of the room noise energy is below 120 Hz (strongest tone 50 Hz); speech has 6% there. A 100 Hz high-pass filter was tested on the same recordings and changed nothing material (Kroko 9.8% to 10.1%, NeMo 8.5% to 8.8% before the spelling fix), so the server does not filter.
- **No hallucinated words:** none of the models produced a word on the four silence/typing/breathing clips.
- **Shared mistakes are usually audio, not models:** "Instead of rushing" was dropped by every model in one clip because the recorder's start threshold was raised by the hum and clipped the beginning. The recorder now measures levels above 100 Hz.
- **Edges matter:** Kroko and NeMo drop a word that begins at the very first sample or ends at the very last. The scorer pads every clip with 0.5 s of silence before and 1.5 s after, as live use does; the server pads 1.5 s when finalizing and the WAV replay adds a 0.5 s lead-in.
- **Kroko's remaining errors:** soundalikes such as "wool" to "woo", "chirping" to "church thing", "walked" to "walk". These are the model's, and the same ones appear across models in a smaller number.
- **License:** Kroko's community weights are CC-BY-SA according to the model card. The repo's `LICENSE` file is empty and its license field says "other", so the terms are not spelled out. Fine for local class use; the files are not redistributed. Nemotron (NVIDIA Open Model License) and the NeMo models (CC-BY-4.0) are the alternatives with clearer terms.
- **Hotwords:** sherpa-onnx can bias a transducer toward named words (`hotwords_file`, `modified_beam_search`), but the research report's claim that it works with Kroko is untested here, and Kroko's download has no BPE vocabulary file, which hotwords normally need.

## Record (separate terminal; it waits for the keyboard)

```bash
uv run --group laptop python scripts/stt_compare.py record --script bench/stt_script.txt
```

1. Stay silent for 3 s (it measures your room's noise floor above 100 Hz; above -40 dBFS is noisy).
2. Read each sentence as soon as it appears. It stops by itself after about a second of quiet and moves on. Each clip shows its length and level, with warnings for too quiet or clipping.
3. Four silence/typing clips at the end (Enter to start each) catch noise hallucinations; skip them with `--no-noise`.
4. Redo bad clips with `record --script bench/stt_script.txt --only 07 12`. `--manual` switches to Enter-to-start/Enter-to-stop, and `--device N` picks an input device (`python -m sounddevice` lists them).

Without `--script` it uses 10 Harry Potter questions and 10 everyday ones. Recordings stay in `bench/mic_turns/` (git-ignored).

## Score

```bash
uv run --group laptop python scripts/stt_compare.py run --fast
```

Candidates: `baseline`, `kroko` (both in `models/`, verified by `scripts/fetch_laptop_models.py`), and `nemo-fc-480ms`, `nemo-fc-80ms`, `nemotron-160ms` in `models/stt-candidates/` (downloaded from Hugging Face by hand; not part of the server's model check). Pick some with `--only`. Drop `--fast` to pace audio at real time (as long as the recordings, per model). `--audio-dir DIR` scores another set of clips.

## Synthetic smoke test (clean Kokoro voices, 6 clips)

Useful only for speed; clean synthetic speech says little about accuracy.

| model | WER | delay after speech p50 | RTF |
|---|---|---|---|
| baseline | 0.077 | 91 ms | 0.11 |
| kroko | 0.000 | 42 ms | 0.04 |
| nemo-fc-480ms | 0.026 | 120 ms | 0.13 |
| nemo-fc-80ms | 0.026 | 275 ms | 0.34 |
| nemotron-160ms | 0.000 | 996 ms | 1.22 |
