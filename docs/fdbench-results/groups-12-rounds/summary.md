# Full-Duplex Voice Agent on a Raspberry Pi

Student me3 · run 2026-11-07_15-33-39 · 2026-11-07

## 5 · Evaluation & Benchmark

| Metric | Definition | Target | Mean | p95 | n |
|---|---|---|---|---|---|
| E2E latency | end of speech → audio out | < 4.00 s | — | — | 0 |
| ASR final | end of speech → transcript | < 0.80 s | — | — | 0 |
| LLM TTFT | transcript → first token | < 1.50 s | — | — | 0 |
| TTS TTFB | text → first audio chunk | < 0.60 s | — | — | 0 |
| Barge-in | speech → playback stopped | < 0.80 s | 0.70 s | 0.90 s | 16 |
| WER | ASR accuracy on test set | < 15 % | — | — | 0 |

- One clock: the assistant machine's monotonic clock; every stage timestamped (VAD end → ASR final → LLM 1st token → TTS 1st audio → speaker)
- E2E = speaker − VAD end; barge-in = stop − speech start
- 0 phrases × 0 runs; mean + p95; JSON log per run: stage_latency.json (per turn: trials/<id>/stages.jsonl)

Notes:
- E2E latency: no data in this run
- ASR final: not reported: the assistant sent no stage marks (docs/STAGES.md)
- LLM TTFT: not reported: the assistant sent no stage marks (docs/STAGES.md)
- TTS TTFB: not reported: the assistant sent no stage marks (docs/STAGES.md)
- Barge-in: 1 interruption(s) she talked through, not counted
- WER: not reported: the assistant sent no stage marks (docs/STAGES.md)

## Results (deck 2)

Latency chart: latency_chart.svg

- RPi model / RAM: Linux 7.2.2-arch1-1 · 46.7 GB
- Cooling: not stated
- Network: cloud: Wi-Fi
- Date: 2026-11-07
- Model versions: ASR Parakeet (lan_speech), LLM router 350M kd-web4 + Gemini, TTS Kitten, VAD Silero

## 3 · Context engineering: context size per run

Not reported: the assistant sent no llm_request marks (docs/STAGES.md).
