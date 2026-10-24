# Fact-check of the Gemini echo response (2026-10-24)

Sources: agy runs B, C, D in this folder (search-only, plan mode, read_url limited to a domain list); run A (WebRTC) timed out at 45 min with no output, so its claims were checked by the assistant directly against current WebRTC `main` (googlesource, last commit 2026-10-23), livekit-rtc 1.1.21 (local uv cache), livekit-agents `main` (PyPI 1.8.8) and the Pipecat 1.13 install in `pipeline/.venv`. agy verdicts marked "search snippet" were not opened pages.

## WebRTC AEC3 (checked in source)

| Claim | Verdict | Evidence |
|---|---|---|
| Delay search covers 640 ms | PARTLY | `num_filters = 5`, `down_sampling_factor = 4`, window 32 sub-blocks, shift 24 sub-blocks (`aec3_common.h:52-54`), 4 ms sub-blocks. Lags = filter index x 96 ms + 0..128 ms, so about **0-512 ms** by my reading (buffer holds 153 sub-blocks = 612 ms). Either way a ~0.65 s delay after the Pi idles is outside it. |
| `set_stream_delay_ms` ignored once locked | PARTLY | The hint is stored (`render_delay_buffer.cc:330-342`) and used as the starting delay after every buffer reset (`:171-188`); the internal estimator then overrides it (`AlignFromDelay`). Calling it once is enough: the value persists. |
| `kMaxApiCallJitterBlocks = 30` / overrun explains lead >= 200 ms | CONTRADICTED (name/value) | No such constant. Real rule: `buffering.max_allowed_excess_render_blocks = 8` checked every `excess_render_detection_interval_blocks = 250`: if render stays more than 8 blocks (32 ms) ahead of capture for a second, `DetectExcessRenderBlocks()` resets the buffer (`:438-459`, `:268-275`). **Our `aec.py` feeds 50 ms of reference ahead (12.5 blocks), so by this reading every reply triggers a reset within about 1 s.** Untested; check offline with `aec_offline.py` at lead 0/30/50. |
| Bulk delay hysteresis / filter 12 blocks (48 ms) | PARTLY | `hysteresis_limit_blocks = 1`, `delay_headroom_samples = 32` confirmed; refined and coarse filters are 13 blocks (52 ms), 12 only initially. `ClockdriftDetector` only classifies drift (none/probable/verified); no compensation found. |
| normal/nearend tuning + dominant near-end detector | CONFIRMED (fields) | `dominant_nearend_detection`: `enr_threshold = .25`, `snr_threshold = 30`, `trigger_threshold = 12` blocks (48 ms), `hold_duration = 50` blocks (200 ms). The 100-250 ms onset damage is not measured anywhere. |
| Linear AEC output exists | CONFIRMED (in WebRTC) | `linear_aec_buffer` in `audio_processing_impl.cc`; not exposed by LiveKit. |
| LiveKit APM exposes 4 booleans + 3 calls | CONFIRMED | livekit-rtc 1.1.21 `apm.py`. |
| PipeWire clock matching, SpeexDSP clock rule, Python bindings | NOT CHECKED | Run A timed out. |

## Frameworks (agy B; LiveKit and Pipecat re-checked in source)

| Claim | Verdict |
|---|---|
| LiveKit `min_interruption_duration` 0.5 s, `min_interruption_words` 0, `resume_false_interruption`, `false_interruption_timeout` 2.0 s, adaptive mode | CONFIRMED in `livekit/agents` `voice/turn.py:162-196` (now `interruption.min_duration`, `min_words`, `mode: "adaptive" | "vad"`, legacy names still accepted) |
| Pipecat `MinWordsInterruptionStrategy` | PARTLY: removed in 1.0; replacement `MinWordsUserTurnStartStrategy` exists in the installed 1.13 |
| Pipecat VADParams `stop_secs = 0.8` | CONTRADICTED: 0.2 (installed 1.13); confidence 0.7, start 0.2, min_volume 0.6 correct |
| Pipecat commits only spoken text (word timestamps) | CONFIRMED (snippet) |
| Vapi `stopSpeakingPlan`, Retell `interruption_sensitivity` | CONFIRMED (snippet) |
| OpenAI Realtime `prefix_padding_ms = 300`; Gemini Live has no server-side echo removal | CONFIRMED (snippet) |
| Duck-then-confirm-then-restore documented anywhere | NOT FOUND |
| Pre-roll: OpenAI 300 ms, LiveKit Silero `prefix_padding_duration` 0.5 s | CONFIRMED (snippet) |

## Neural, measurement, hardware (agy C, D)

| Claim | Verdict |
|---|---|
| NKF-AEC citation | WRONG: Yang et al., Tencent GVoice, ICASSP 2023, arXiv:2207.11388, 5.3K params, `fjiang9/NKF-AEC`, needs external delay compensation |
| DTLN-aec citation | WRONG venue/arXiv: ICASSP 2021, arXiv:2010.14337; TFLite only; trained on 10-100 ms delays |
| DeepVQE citation | WRONG authors: Indenbom, Ristea et al.; alignment up to 1 s; community weights only (`richiejp/deepvqe-ggml`) |
| Open neural AEC tolerating 100-650 ms + drift | NOT FOUND |
| Speaker embedding on 300 ms rejects the bot's voice | CONTRADICTED: EER over 25-35% at 0.3-0.5 s |
| AECMOS | CONFIRMED: MIT, ONNX, 16 kHz, reference + mic + enhanced in, echo and degradation scores out |
| Keep ALSA open with silence/dither | CONFIRMED mechanism; the cause of our 0.5 s wake-up is not established |
| Tap reference after the volume stage | CONFIRMED only by a search snippet (Biamp guide) |
| ReSpeaker v2.0 | CONFIRMED: XVF-3000, ~$64, AEC only for audio played through its own jack, 16 kHz playback on stock firmware |
| XVF3800 tail 150 ms | PARTLY: 192 ms (weak source) |
| Jabra Speak 510 full duplex | PARTLY: reportedly half duplex; Speak2, PowerConf, Poly Sync 20 full duplex |
| Measured barge-in with any of this hardware on a Pi | NOT FOUND |

Every hardware canceller needs the microphone and the speaker on the same device: buying one means moving the microphone to the Pi side.
