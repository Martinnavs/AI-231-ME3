# Checks run against the agy reports (2026-10-20, Kroko, bench/mic_turns, 28 clips / 387 words, 2 threads)

| Config | WER |
|---|---|
| greedy (baseline, reproduces 9.8%) | 9.82% |
| greedy + blank_penalty 0.5 / 1.0 / 2.0 | 10.6 / 10.3 / 10.6% (worse; agy claimed it helps) |
| modified_beam_search, 4 paths | 9.04% (3 words better; within noise) |
| mbs4 + blank_penalty 0.5 / 1.0 / 2.0 | 9.3 / 11.4 / 12.4% (worse) |

- Kroko files here are encoder.onnx / decoder.onnx / joiner.onnx (fp32), not *.int8.onnx as agy said.
- sherpa-onnx 1.13.8 exposes hotwords_file, hotwords_score, blank_penalty, lm, lm_shallow_fusion, bpe_vocab, modeling_unit.
- result.ys_probs exists but holds LOG-probs (negative); confidence = exp(). result.words, timestamps, lm_probs, context_scores also exist.
- Hotwords: sherpa rejects my hand-made token sequences ("Cannot find ID for token sa"); no effect measured. Untested: modeling_unit="bpe" with a bpe_vocab file generated from tokens.txt. The test set has only 2 entities (arthur, saturday), so it cannot measure biasing anyway.
- agy citation problems: arXiv 2412.08587 labelled "Dec 2025"; many "MEASURED" tags without measurements.

## Hotwords on Kroko (synthetic Kokoro clips, 5 sentences; mbs 4 paths)
- Pre-tokenized hotwords fail: sherpa splits "▁jo" into "▁ jo" and rejects pieces ("Cannot find ID for token jo"). Score must be last on the line; "@tag" is not accepted.
- Plain phrases with modeling_unit="bpe" + bpe_vocab generated from tokens.txt (3 case forms each) load without error but the real BPE merges are unknown, so segmentation does not match what the model emits.
- Score 3: only "Up Dharma Down" fixed (and "Tadhana" worse). Score 6-12: new errors ("Paulite", "Panter Picco", "AnGilo", "jaliby"); no reliable gain.
- Verdict: hotwords are not usable with Kroko as shipped. Baseline errors are all near-misses of a closed entity list ("opalate", "Angeilo", "Pantropico", "Binnie"), which suggests post-STT phonetic matching instead.
- Hard test set: bench/stt_hard.json (59 clips, explicit multi-word entities). Record with:
  uv run --group laptop python scripts/stt_compare.py record --items bench/stt_hard.json --audio-dir bench/mic_hard --no-noise
  Score with: ... run --audio-dir bench/mic_hard --tag hard

## Hard set baseline (bench/mic_hard, 59 real-voice clips, 83 entity mentions, 2 threads, --fast)
| model | WER | entities | final p50 ms | RTF |
|---|---|---|---|---|
| kroko | 21.9% | 47% (39/83) | 45 | 0.05 |
| nemo-fc-480ms | 27.5% | 47% (39/83) | 131 | 0.13 |
Kroko per category: music 11/30, contact 5/15, place 1/5, brand 4/8, number 8/10, everyday 4/4, correction 6/11.

## Crude phonetic entity matcher (scratchpad match.py; catalog = ~230 entities incl. the test ones)
Kroko entities 47% -> 71% (distance<=0.2, WER unchanged 21.9->21.6%) -> 78% (<=0.3, but WER 35% from false replacements).
NeMo-480 likewise 47% -> 72% / 76%. Caveat: threshold picked on the same test set; catalog contains the answers; real catalogs are bigger.

## Offline candidates on the same sets (sherpa-onnx int8, 4 threads, whole clip decoded after end of speech; script: scratchpad offline.py)
| model | hard WER | hard entities | easy WER | decode p50 hard / easy | RTF |
|---|---|---|---|---|---|
| parakeet-tdt-0.6b-v2 | 12.3% | 50/83 (60%) | 3.6% | 377 / 629 ms | 0.078 |
| parakeet-tdt-0.6b-v3 | 15.0% | 47/83 (57%) | 4.9% | 408 / 671 ms | 0.082 |
| moonshine-base (v1) | 19.7% | 45/83 (54%) | 13.7% | 231 / 355 ms | 0.045 |
(kroko: hard 21.9% / 47%, easy 9.8%; nemo-480: 27.5% / 47%.)
Scorer artifact: Parakeet writes digits ("8", "8 PM"), refs are spelled out, so its number category (3/10) and WER are understated. Needs digit<->word normalisation in norm_words before final numbers.
With the crude matcher (<=0.2): parakeet-v2 entities 60% -> 80% (WER 12.3 -> 14.0%), v3 57 -> 76%, moonshine 54 -> 73%.
Models in models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v{2,3}-int8 and sherpa-onnx-moonshine-base-en-int8 (downloaded from csukuangfj/ on HF).

## After digit normalisation (scripts/stt_compare.py norm_words now spells digits, times, %, SB19; test added)
| model | hard WER | hard entities | easy WER |
|---|---|---|---|
| parakeet-v2 | 8.3% | 60/83 (72%) | 3.6% |
| parakeet-v3 | 13.2% | 50/83 (60%) | 4.9% |
| moonshine-base | 19.6% | 45/83 (54%) | 13.7% |
| kroko | 21.8% | 47% | 9.8% |
| nemo-fc-480ms | 26.7% | 47% | 8.0% |
Earlier hard-set figures for Parakeet were understated by digit output; the table above supersedes them.

## Network-resilience reports (agy x3: AGY-NET-PRODUCTS / PAPERS / PRACTICE) - spot checks of arXiv abstracts
- 2511.07397 ConvFill: exists, authors right, small on-device talker + external reasoner; abstract gives NO number ("millisecond-level"); report's "sub-200 ms" is unsupported; talker ran on an Apple M2, not Pi-class hardware.
- 2606.13450 Endpoint Anticipation: exists; 505 ms average latency reduction confirmed, at the cost of +28.4% speculative computation (report omitted the cost).
- 2609.17416 Never Stop Thinking: exists; "19% overall, by half in the targeted regime" confirmed.
- 2512.12769 ASTA: exists; routes by CPU load, device temperature, network latency; the specific thresholds the report quotes (>150 ms RTT, >80% CPU, >50 C) are NOT in the abstract.
- Nearly every report claim is tagged VERIFIED-IN-SNIPPET; treat as unverified unless listed here.
- Reports DISAGREE on Pi<->laptop reachability over the phone hotspot (products report assumes it works; practice report says AP isolation is certain). Neither verified; test it.
- Hotwords "ADOPT" in the papers report contradicts our Kroko tests; untested for Parakeet.

## Parakeet improvement reports (AGY-PARAKEET-IMPROVE / AGY-PARETO-REPAIR) - checked
- FALSE in the report: "sherpa-onnx hotwords / modified_beam_search are broken for NeMo TDT". Both run. modified_beam_search (4 paths) changed 10/59 clips (WER 8.3 -> 8.8%, entities unchanged); with modeling_unit="bpe" + a bpe_vocab built from tokens.txt, hotwords (317 entries) took WER 8.3 -> 4.7% and entities 72 -> 88% at score 1.0 (+~50 ms); easy set unchanged at 3.6%. Scripts: scripts/hotp2.py, hotp3.py, hotp5.py (+dis_list.txt).
- TRUE: result.ys_log_probs exists (offline). Word-level geo-mean confidence AUROC 0.87 for correct vs wrong words (408 words, 30 wrong); conf<0.9 flags 16% of words, recalls 73% of errors, 33% precision. Script: scripts/conf.py.
- Unverified/background: LoRA 15-30% gains, 1.5-3 A100 hours, Epitran/phonemizer usage, PSGC counts (1,742 LGUs), "gazetteers above 5-10k entities lose precision", request-share percentages (synthesised, not published).

## Held-out hotword check (bench/stt_heldout.json, 36 clips, list frozen in bench/hotwords_v1.txt)
greedy 11.6% WER, in-list 10/16, out-of-list 15/27 -> hotwords 1.0: 9.3%, 13/16, 14/27. By kind: in-list 13.8->5.0%, out-of-list 19.7->22.7%, open-world 6.9->5.6%, complex 7.5->6.5%. Criteria met; sample is small. Scorer fixed for thousands separators and numbers up to 999,999.

## Repair ladder evaluation (scripts/stt_repair_eval.py)
Unguarded matcher on hotword output: false fixes 18 (hard) / 31 (held-out), names broken 11 / 10, WER 4.7->9.6% / 9.3->21.5%. After protecting right names, CMUdict guard, length gate and slot awareness: held-out 0 false, 0 broken, WER 9.3->9.3; hard +2 entities. Greedy-pass confidence made no difference. The earlier prototype numbers (71-80%) are superseded.
