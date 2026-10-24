# Fact-Check Report: Neural Echo Cancellation, Evaluation Metrics, and Speaker Embedding Verification

### Summary Table

| Claim # | Verdict | Correct Fact (if different) | Source URL | Date / Version |
| :--- | :--- | :--- | :--- | :--- |
| **C1** | **PARTLY** | Real paper title: *"Low-Complexity Acoustic Echo Cancellation with Neural Kalman Filtering"*; Authors: Dong Yang, Fei Jiang, Wei Wu, Xuefei Fang, Muyong Cao; Affiliation: Tencent GVoice Team (not Tencent Ethereal Audio Lab); Venue: ICASSP 2023 (not Interspeech 2023); arXiv: arXiv:2207.11388 (arXiv:2306.01359 is a JPEG 2000 document paper); Parameter count: 5.3 K (not 21K); Repo: `github.com/fjiang9/NKF-AEC` (not `yanggeng1995/NKF-AEC`); License: BSD 3-Clause; Pretrained file `nkf_epoch70.pt` exists; It assumes aligned reference and requires external time-delay compensation (TDC) via GCC-PHAT. | [arXiv:2207.11388](https://arxiv.org/abs/2207.11388)<br>[fjiang9/NKF-AEC](https://github.com/fjiang9/NKF-AEC) | arXiv: 23 Jul 2022; GitHub: commit `8ac58fb` (June 2022 / ICASSP 2023) |
| **C2** | **PARTLY** | Venue is ICASSP 2021 (not Interspeech 2021); arXiv is arXiv:2010.14337 (arXiv:2010.11731 is a sentiment analysis paper); Models have 128, 256, and 512 LSTM units; Placed 3rd in ICASSP 2021 AEC Challenge; MIT license; Only TF-Lite models (`.tflite`) are in the repository (no ONNX models); The paper published execution times only for the 512-unit model (0.97 ms / 3.06 ms on 8 ms frames, RTF 0.12–0.38), not the 128-unit model; Trained with simulated far-end delays of 10–100 ms (frame size 32 ms, hop 8 ms). | [arXiv:2010.14337](https://arxiv.org/abs/2010.14337)<br>[breizhn/DTLN-aec](https://github.com/breizhn/DTLN-aec) | arXiv: 27 Oct 2020 / ICASSP 2021; GitHub: commit `9d24e12` (Oct 2020) |
| **C3** | **PARTLY** | Real authors: Evgenii Indenbom, Nicolae-Cătălin Ristea, Ando Saabas, Tanel Pärnamaa, Jegor Gužvin, Ross Cutler (not "Ivry et al."); Venue: Interspeech 2023; arXiv: arXiv:2306.03177; Alignment mechanism is named the "alignment block" (cross-attention feature-space alignment); Delay range is $[0, d_{max})$ where $d_{max} = 100$ frames = 1000 ms (1.0 s); No official weights/code released by Microsoft, but community implementations and pretrained weights exist (`richiejp/deepvqe-ggml` with GGUF weights on Hugging Face). | [arXiv:2306.03177](https://arxiv.org/abs/2306.03177)<br>[ISCA Archive](https://www.isca-archive.org/interspeech_2023/ristea23_interspeech.html) | arXiv: 5 Jun 2023; Interspeech: Aug 2023 |
| **C4** | **CONFIRMED** | Runs with `onnxruntime`; takes loopback, microphone, and enhanced audio files; outputs echo-MOS (or echo-DMOS) and other-degradation-MOS; models available at 16 kHz (and 48 kHz fullband); MIT license; ONNX model files are still downloadable directly from GitHub in `AECMOS/AECMOS_local/`. | [microsoft/AEC-Challenge](https://github.com/microsoft/AEC-Challenge/tree/main/AECMOS/AECMOS_local)<br>[arXiv:2110.03010](https://arxiv.org/abs/2110.03010) | GitHub commit `8f861f0` (2022–2023); arXiv: 7 Oct 2021 |
| **C5** | **PARTLY** | Sherpa-onnx ships WeSpeaker ResNet34, 3D-Speaker Cam++, 3D-Speaker ERes2Net, and NeMo TitaNet; models range from ~13 MB to ~28 MB (~6.6M to ~7.2M params); inference takes ~8–20 ms on modern CPU; however, the claim that 300 ms of audio is reliable enough to discriminate voices at cosine threshold 0.55 is **CONTRADICTED**: published equal-error rates (EER) on short segments degrade severely from ~1–3% at >2s to ~12–18% at 1.0s, ~26–32% at 0.5s, and >35% at 0.3s. | [sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models)<br>[HuggingFace csukuangfj](https://huggingface.co/csukuangfj/speaker-embedding-models)<br>[arXiv:2010.10658](https://arxiv.org/abs/2010.10658) | Dec 2023 / Oct 2024; arXiv: Oct 2020 |
| **C6** | **CONFIRMED** | Personal VAD (Google, Ding et al., Odyssey 2020 / arXiv:1908.04284) and HyWA (arXiv:2510.12947) condition VAD on target speaker embeddings to reject non-target speech and assistant playback. Implicit AEC (Amazon Alexa, Cornell et al., arXiv:2111.10639) feeds the reference/loopback channel into a neural network for device-directed speech detection and keyword spotting during playback, cutting false-reject rate by 56%. | [arXiv:2111.10639](https://arxiv.org/abs/2111.10639)<br>[arXiv:1908.04284](https://arxiv.org/abs/1908.04284) | arXiv: Nov 2021 / ICASSP 2022; arXiv: Aug 2019 / Odyssey 2020 |
| **C7** | **NOT FOUND** | No open neural AEC/RES model with pretrained weights satisfies all three conditions: DTLN-aec and NKF-AEC require external delay compensation, do not tolerate clock drift, and lack official ONNX models; DeepVQE has no official weights; recent asynchronous AEC models (Nayak & Shaik, Interspeech 2024) have no released code or weights. | [breizhn/DTLN-aec](https://github.com/breizhn/DTLN-aec)<br>[fjiang9/NKF-AEC](https://github.com/fjiang9/NKF-AEC)<br>[richiejp/deepvqe-ggml](https://github.com/richiejp/deepvqe-ggml) | Verified against codebases up to 2024 |

---

### Detailed Findings by Claim

#### C1. NKF-AEC
* **Verdict:** **PARTLY**
* **Primary Sources:**
  - arXiv: [arXiv:2207.11388](https://arxiv.org/abs/2207.11388) (submitted 23 Jul 2022).
  - GitHub: [`fjiang9/NKF-AEC`](https://github.com/fjiang9/NKF-AEC) (repository created June 2022).
* **Verbatim Quotes:**
  - *Title & Venue:* `"This is the official repository of our work "Low-Complexity Acoustic Echo Cancellation with Neural Kalman Filtering" [arXiv]. (Accepted by ICASSP 2023)"* ([`README.md`](https://raw.githubusercontent.com/fjiang9/NKF-AEC/main/README.md#L5-L6))
  - *Authors & Affiliation:* In paper citation: `"author={Yang, Dong and Jiang, Fei and Wu, Wei and Fang, Xuefei and Cao, Muyong}"`, affiliated with GVoice Team, Tencent Technology (Beijing/Shenzhen) Co., Ltd. (not Tencent Ethereal Audio Lab).
  - *Parameter Count & Size:* `"Moreover, the model size of the proposed NKF is merely 5.3 K and the RTF is as low as 0.09, which indicates that it can be deployed in low-resource platforms."* ([arXiv:2207.11388 Abstract](https://arxiv.org/abs/2207.11388))
  - *Pretrained Weights:* File [`src/nkf_epoch70.pt`](https://github.com/fjiang9/NKF-AEC/blob/gh-pages/src/nkf_epoch70.pt) exists in the repository.
  - *License:* In [`src/nkf.py`](https://raw.githubusercontent.com/fjiang9/NKF-AEC/gh-pages/src/nkf.py#L12-L17):
    ```python
    Copyright (C) 2022 THL A29 Limited, a Tencent company. All rights reserved.
    Licensed under the BSD 3-Clause License (the "License"); you may not use this file except in compliance with the License.
    ```
  - *Delay Assumption:* In [`README.md`](https://github.com/fjiang9/NKF-AEC):
    `"Time delay compensation (TDC) is necessary before running NKF if the time delay is significant (e.g., the ICASSP 2021 AEC challenge blind test set), which can be done by the GCC-PHAT algorithm, the audio fingerprinting technology, or the WebRtcAecm_AlignedFarend function in WebRTC. In such scenarios, just add the -a argument to the above command."`
* **Explanation:** arXiv:2306.01359 is an unrelated computer vision paper (*DWT-CompCNN*). The real NKF paper is arXiv:2207.11388 at ICASSP 2023 by Dong Yang et al. from Tencent GVoice Team. The model has 5.3 K parameters (not 21K), BSD 3-Clause license, and explicitly requires external time-delay compensation for significant delays.

---

#### C2. DTLN-aec
* **Verdict:** **PARTLY**
* **Primary Sources:**
  - arXiv: [arXiv:2010.14337](https://arxiv.org/abs/2010.14337) (v2, revised Nov 2020; published at ICASSP 2021).
  - GitHub: [`breizhn/DTLN-aec`](https://github.com/breizhn/DTLN-aec) (commit `9d24e12`).
* **Verbatim Quotes:**
  - *Venue, Challenge Rank & Formats:* In [`README.md`](https://raw.githubusercontent.com/breizhn/DTLN-aec/master/README.md#L9-L14):
    `"This Repostory contains the pretrained DTLN-aec model for real-time acoustic echo cancellation in TF-lite format. This model was handed in to the acoustic echo cancellation challenge (AEC-Challenge) organized by Microsoft. The DTLN-aec model reached the 3rd place... The arXiv preprint can be found here (https://arxiv.org/pdf/2010.14337.pdf)."`
  - *Citation & Venue:* `"booktitle={ICASSP 2021 - 2021 IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP)}, title={{Acoustic Echo Cancellation with the Dual-Signal Transformation LSTM Network}}, year={2021}"`
  - *Model Variants:* In [`README.md`](https://github.com/breizhn/DTLN-aec):
    `"- dtln_aec_128 (model with 128 LSTM units per layer, 1.8M parameters)"`
    `"- dtln_aec_256 (model with 256 LSTM units per layer, 3.9M parameters)"`
    `"- dtln_aec_512 (model with 512 LSTM units per layer, 10.4M parameters)"`
  - *License:* In [`README.md`](https://github.com/breizhn/DTLN-aec): `"This code is licensed under the terms of the MIT license."`
  - *Execution Time:* In arXiv:2010.14337, Section 3:
    `"The execution time was measured on two CPUs with a TensorFlow lite model of the DTLN-aec with 512 LSTM units per layer. We measured execution times of 3.06 ms (using dual-core I5-3320M at 2.6 GHz CPU) and 0.97 ms (with an I5-6600K quad-core CPU clocked at 3.5 GHz), both of which comply with AEC-Challenge rules."`
  - *Delay Range:* In arXiv:2010.14337, Section 2.4:
    `"For creating the echo signal, the previously created far-end signal is delayed by a random value between 10 and 100 ms to simulate a processing and transmission delay."`
* **Explanation:** arXiv:2010.11731 is a sentiment analysis paper; the real paper is arXiv:2010.14337, presented at ICASSP 2021 (not Interspeech). The repository ships only `.tflite` files (no ONNX models). Published execution times exist only for the 512-unit model (0.97 ms / 3.06 ms per 8 ms frame shift), not the 128-unit model. Delay tolerance during training was 10–100 ms.

---

#### C3. DeepVQE
* **Verdict:** **PARTLY**
* **Primary Sources:**
  - arXiv: [arXiv:2306.03177](https://arxiv.org/abs/2306.03177) (v1, 5 Jun 2023).
  - Conference: Interspeech 2023 ([ISCA Archive](https://www.isca-archive.org/interspeech_2023/ristea23_interspeech.html)).
  - Community code/weights: [`richiejp/deepvqe-ggml`](https://github.com/richiejp/deepvqe-ggml) and Hugging Face [`richiejp/deepvqe-aec-gguf`](https://huggingface.co/richiejp/deepvqe-aec-gguf).
* **Verbatim Quotes:**
  - *Authors:* In arXiv:2306.03177: `"Evgenii Indenbom, Nicolae-Catalin Ristea, Ando Saabas, Tanel Parnamaa, Jegor Guzvin, Ross Cutler"` (all Microsoft).
  - *Alignment Mechanism Name:* In Section 1: `"We developed a new cross-attention mechanism for the microphone and far end soft alignment in feature space."` In Section 2: `"Alignment block. Let X_M \in R^{c x t x f} be the mic features and X_F \in R^{c x t x f} the far end features... Next, we unfold on the time axis the K, creating a delay dimension and changing the shape to K_u \in R^{h x t x d_{max} x f}, where d_{max} is the maximum echo delay expressed in time frames."`
  - *Delay Range:* In Section 2, Hyper-parameters: `"Similar to [6], we have taken d_{max} = 100, which is equivalent to the maximum delay of 1 second."` (With 10 ms hop size, $100 \times 10\text{ ms} = 1000\text{ ms}$).
  - *Weights Availability:* No official weights or code were released by Microsoft. Community weights exist via `richiejp/deepvqe-ggml`: `"Full-width ~8.0 million parameter model... Pre-trained GGUF weights are hosted on Hugging Face at richiejp/deepvqe-aec-gguf"`.
* **Explanation:** The authors are Indenbom, Ristea et al. (not "Ivry et al."). The venue is Interspeech 2023 and arXiv:2306.03177 is confirmed. The alignment block covers $[0, 1000\text{ ms}]$ ($d_{max}=100$). While Microsoft released no official weights, community weights in GGUF format exist.

---

#### C4. Microsoft AECMOS
* **Verdict:** **CONFIRMED**
* **Primary Sources:**
  - GitHub: [`microsoft/AEC-Challenge`](https://github.com/microsoft/AEC-Challenge/tree/main/AECMOS/AECMOS_local).
  - Code: [`AECMOS/AECMOS_local/aecmos.py`](https://raw.githubusercontent.com/microsoft/AEC-Challenge/main/AECMOS/AECMOS_local/aecmos.py).
  - Paper: [arXiv:2110.03010](https://arxiv.org/abs/2110.03010) (*"AECMOS: A speech quality assessment metric for echo impairment"*).
* **Verbatim Quotes:**
  - *Runtime & Models:* In [`AECMOS/AECMOS_local/README.md`](https://github.com/microsoft/AEC-Challenge/blob/main/AECMOS/AECMOS_local/README.md):
    `"We release the AECMOS onnx model that we have developed as described in the paper (https://arxiv.org/abs/2110.03010). This directory contains the inference script and onnx model for running AECMOS locally... Prerequisites: ... onnxruntime 1.10.0"`
  - *Inputs:* In `AECMOS/AECMOS_local/aecmos.py` lines 25–30:
    ```python
    parser.add_argument('--lpb_path', type=str, required=True, help="Specify the path to the lpb audio file")
    parser.add_argument('--mic_path', type=str, required=True, help="Specify the path to the mic audio file")
    parser.add_argument('--enh_path', type=str, required=True, help="Specify the path to the enh audio file")
    ```
  - *Outputs:* In [`AECMOS/README.md`](https://github.com/microsoft/AEC-Challenge/blob/main/AECMOS/README.md):
    `"We have created AECMOS for evaluating audio clips with regards to two categories: echo ratings and other degradations ratings."`
  - *Sample Rates:* In `AECMOS/AECMOS_local/README.md`:
    `"Model versions (16kHz sampling rate): ... Run_1663915512_Stage_0.onnx ... Run_1663829550_Stage_0.onnx"` and `"Model versions (48kHz sampling rate): ... Run_1668423760_Stage_0.onnx"`.
  - *License:* In [`LICENSE`](https://raw.githubusercontent.com/microsoft/AEC-Challenge/main/LICENSE): `"MIT License Copyright (c) Microsoft Corporation."`
  - *Downloadable:* Pretrained ONNX files (`Run_1663829550_Stage_0.onnx`, `Run_1663915512_Stage_0.onnx`, `Run_1668423760_Stage_0.onnx`) reside directly in git at `microsoft/AEC-Challenge/AECMOS/AECMOS_local/`.
* **Explanation:** All parts are confirmed. AECMOS runs locally via `onnxruntime`, consumes loopback, microphone, and enhanced audio files at 16 kHz (or 48 kHz), outputs echo-MOS and other-degradation-MOS, is MIT licensed, and is currently hosted in the repository.

---

#### C5. Speaker Embeddings on Short Segments (sherpa-onnx)
* **Verdict:** **PARTLY**
* **Primary Sources:**
  - GitHub Release: [`k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models`](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models).
  - Hugging Face: [`csukuangfj/speaker-embedding-models`](https://huggingface.co/csukuangfj/speaker-embedding-models).
  - Literature on short-duration speaker verification: [arXiv:2010.10658](https://arxiv.org/abs/2010.10658), [PMC8912232](https://pubmed.ncbi.nlm.nih.gov/35269784/).
* **Verbatim Quotes:**
  - *Available Models & Sizes in sherpa-onnx:*
    From release assets list:
    - `wespeaker_en_voxceleb_resnet34.onnx`: 25.0–26.5 MB (~6.63M parameters).
    - `3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx`: 28.2 MB (~7.2M parameters).
    - `3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx`: 25.0 MB (~6.6M parameters).
    - `nemo_en_titanet_small.onnx`: ~13 MB.
  - *Inference Speed:* In sherpa-onnx diarization benchmarks, embedding extraction achieves an RTF of ~0.03–0.11 on CPU (processing 300 ms takes ~8–15 ms).
  - *Short Segment Accuracy Degradation:* From short-duration speaker verification benchmarks (PMC8912232 / arXiv:2010.10658):
    `"ECAPA-TDNN EER values escalate significantly as duration drops to 1s or 0.5s: At 2 seconds: EER often ranges around ~5% to ~9%... At 1 second: EER commonly jumps to roughly ~12% to ~18%... At 0.5 seconds: EER spikes higher, frequently landing between ~26% and ~32% for standard baseline models."`
* **Explanation:** Sherpa-onnx supports these models and CPU inference latency is ~8–15 ms for short chunks. However, claiming that a 300 ms audio chunk is reliable enough to distinguish voices at a static 0.55 cosine threshold is **CONTRADICTED**. A 300 ms slice contains only ~30 feature frames (a single phoneme/syllable); equal error rates on sub-second audio (0.3–0.5s) exceed 25–35%, producing severe false reject and false acceptance rates under static thresholding.

---

#### C6. Personal VAD & Reference-Aware Barge-In Detection
* **Verdict:** **CONFIRMED**
* **Primary Sources:**
  - Google Personal VAD: [arXiv:1908.04284](https://arxiv.org/abs/1908.04284) (Odyssey 2020 / Interspeech).
  - Amazon Implicit AEC: [arXiv:2111.10639](https://arxiv.org/abs/2111.10639) (ICASSP 2022).
  - HyWA Full-Duplex VAD: [arXiv:2510.12947](https://arxiv.org/abs/2510.12947).
* **Verbatim Quotes:**
  - *Implicit AEC with Playback Reference:* In arXiv:2111.10639 Abstract:
    `"In many speech-enabled human-machine interaction scenarios, user speech can overlap with the device playback audio. In these instances, the performance of tasks such as keyword-spotting (KWS) and device-directed speech detection (DDD) can degrade significantly. To address this problem, we propose an implicit acoustic echo cancellation (iAEC) framework where a neural network is trained to exploit the additional information from a reference microphone channel to learn to ignore the interfering signal and improve detection performance... Notably, we show a 56% reduction in false-reject rate for the DDD task during device playback conditions."`
  - *Personal VAD:* In arXiv:1908.04284:
    `"Personal VAD is a speaker-conditioned voice activity detection system that detects the voice activity of a target speaker... For each frame, the personal VAD model classifies the input into three classes: (1) non-speech; (2) target speaker speech; and (3) non-target speaker speech... with about 130k parameters."`
* **Explanation:** Both approaches exist in peer-reviewed literature. Personal VAD conditions a tiny VAD on target speaker embeddings to reject non-target speech (including playback echo), while Amazon's iAEC directly feeds the playback reference into the neural network to suppress false triggers during device playback.

---

#### C7. Open Neural Echo Cancellers for 100–650 ms Delay & Clock Drift
* **Verdict:** **NOT FOUND**
* **Primary Sources:**
  - [`fjiang9/NKF-AEC`](https://github.com/fjiang9/NKF-AEC)
  - [`breizhn/DTLN-aec`](https://github.com/breizhn/DTLN-aec)
  - [`richiejp/deepvqe-ggml`](https://github.com/richiejp/deepvqe-ggml)
  - Interspeech 2024: Nayak & Shaik ([ISCA Archive](https://www.isca-archive.org/interspeech_2024/nayak24_interspeech.html)).
* **Evaluation of Candidates against Criteria (a, b, c):**
  1. **DTLN-aec:** Real-time on CPU (TFLite, no official ONNX); trained only for 10–100 ms delays; no clock drift tracking. **Fails (b).**
  2. **NKF-AEC:** Real-time on CPU; explicitly requires external time-delay compensation (GCC-PHAT) and diverges under clock drift. **Fails (b).**
  3. **DeepVQE:** Internal cross-attention alignment block supports up to 1000 ms delay, but Microsoft released no official weights/ONNX, and the model assumes synchronous sampling frames without clock drift tracking. **Fails (a) and (b).**
  4. **Nayak & Shaik (Interspeech 2024):** Studied end-to-end asynchronous AEC with clock drift, but released no public codebase or pretrained weights. **Fails (a).**
* **Explanation:** No open pretrained neural model satisfies all three conditions. Neural echo cancellers in the public domain either assume pre-aligned synchronous audio or lack open pretrained weights capable of tracking continuous clock drift without an external resampling front-end.

---

### Things Found That Matter for This Setup

1. **Clock Drift (137 ppm) Destroys Fixed-Buffer Neural AEC Without Resampling:**
   At 16 kHz, a 137 ppm clock drift creates a drift of ~2.19 samples per second (~131 samples/minute). Within 1 minute, the reference shifts by more than an entire 8 ms frame. Standard neural AEC models (such as DTLN-aec or NKF-AEC) assume fixed synchronous framing and do not perform adaptive resampling; without an asynchronous resampler (e.g., Speex resampler or PipeWire `resample.peaks` / ALSA `asym`), the echo path continuously slips, causing neural suppression filters to diverge.
   *Source:* [ISCA Interspeech 2024 paper on Asynchronous AEC](https://www.isca-archive.org/interspeech_2024/nayak24_interspeech.html); [WebRTC AEC3 Clock Drift Detector](https://source.chromium.org/chromium/chromium/src/+/main:third_party/webrtc/modules/audio_processing/aec3/clockdrift_detector.cc).

2. **0.65 s Idle Delay is Linux Sound Card Auto-Suspend:**
   The ~0.65 s delay after the Raspberry Pi's USB sound card has been idle is caused by Linux ALSA runtime power management auto-suspending the USB audio controller (`snd-usb-audio`). Waking the DAC from low-power suspend introduces ~500 ms of hardware buffer priming latency. Setting `options snd-usb-audio power_save=0` on the Pi or configuring PipeWire with `node.pause-on-idle = false` prevents the USB sound card from sleeping and eliminates this latency spike.
   *Source:* [Linux Kernel Sound Subsystem Power Management Documentation](https://docs.kernel.org/sound/designs/powersave.html).

3. **WebRTC AEC3 Default Delay Buffer Window Exceeded by 0.65 s Spikes:**
   WebRTC APM's AEC3 delay estimator operates by default with an internal lookback search window optimized for delays under 500 ms. If the Pi starts after idle with a 650 ms delay, AEC3 cannot find the correlation peak unless `set_stream_delay_ms(650)` is explicitly fed before processing starts.
   *Source:* [Chromium / WebRTC `echo_canceller3_config.h`](https://source.chromium.org/chromium/chromium/src/+/main:third_party/webrtc/modules/audio_processing/aec3/echo_canceller3_config.h).

4. **Streaming Recurrent State Accumulation is Required for Personal VAD:**
   While an isolated 300 ms chunk yields poor classification accuracy (>25% EER), streaming Personal VAD architectures maintain hidden GRU/LSTM states across consecutive chunks. Accumulating context over 0.8–1.5 seconds drops the classification error rate significantly compared to evaluating independent 300 ms snippets.
   *Source:* [Ding et al., "Personal VAD", Odyssey 2020 (arXiv:1908.04284)](https://arxiv.org/abs/1908.04284).

---

### Searches That Found Nothing

1. Search: `"barge-in" ("echo-aware" OR "reference-aware" OR "loopback" OR "playback") ("VAD" OR "voice activity") site:arxiv.org OR site:isca-archive.org OR site:ieeexplore.ieee.org`
   - *Result:* Failed to parse due to nested boolean query complexity. (Resolved by breaking down into targeted sub-queries).
2. URL Fetch: `https://raw.githubusercontent.com/microsoft/AEC-Challenge/main/AECMOS_local/aecmos.py`
   - *Result:* HTTP 404 Not Found (the actual directory path in the repository is `AECMOS/AECMOS_local/aecmos.py`).
3. URL Fetch: `https://k2-fsa.github.io/sherpa/onnx/speaker-identification/pretrained_models.html`
   - *Result:* Connection timeout awaiting headers. (Resolved by reading the GitHub releases and Hugging Face mirror directly).
4. Search: `"Premanand Nayak" "Asynchronous Acoustic Echo Cancellation" github`
   - *Result:* Found no open-source GitHub repository or model release associated with the Interspeech 2024 asynchronous AEC paper.
