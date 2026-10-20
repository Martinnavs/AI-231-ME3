# STT accuracy and hosting: findings so far (2026-10-20)

**Status: research, no architecture change committed.** Everything here was measured on one speaker and one laptop unless marked otherwise. Companion to `STT-COMPARE.md` (streaming models on easy speech) and `LAPTOP-SPEECH.md` (the first CPU feasibility gate).

## Why this exists

STT text feeds the LLM, so a wrong word corrupts the agent's input. Kroko (the current default) scored 9.8% WER on a plain-English paragraph but was never tested on what the agent is actually asked: names, brands, places, times. We also had to decide where STT should run, because the Pi may run no model except the VAD (professor's rule) and the demo network is a phone hotspot on mobile data.

## Hard test set

`bench/stt_hard.json`: 59 sentences, 83 scored entity mentions, spoken by one person (the owner), mean clip 3.0 s, max 4.8 s. Categories: music (artists, songs, including Filipino acts), contacts (Filipino names), places, brands, numbers and times, everyday commands, and mid-sentence corrections. Entities are listed explicitly per sentence; multi-word entities ("taylor swift") match as phrases.

Record and score:

```bash
uv run --group laptop python scripts/stt_compare.py record --items bench/stt_hard.json --audio-dir bench/mic_hard --no-noise
uv run --group laptop python scripts/stt_compare.py run --only kroko nemo-fc-480ms --fast --audio-dir bench/mic_hard --tag hard
```

Audio is git-ignored (`*.wav`): it is the owner's voice. `norm_words` now spells out digits ("8" = "eight", "6:45" = "six forty five", "40%" = "forty percent", "SB19" = "sb nineteen") on both sides, so models that write digits are not penalised.

## Results (laptop, i5-1145G7, CPU only)

| model | type | hard WER | hard entities found | easy WER | wait after speech ends |
|---|---|---|---|---|---|
| **Parakeet-TDT 0.6B v2 int8** | offline | **8.3%** | **60/83 (72%)** | **3.6%** | ~390 ms (4 threads) |
| Parakeet-TDT 0.6B v3 int8 | offline | 13.2% | 50/83 (60%) | 4.9% | ~420 ms |
| **Parakeet v2 + hotwords** (see below) | offline | **4.7%** | **73/83 (88%)** | 3.6% | ~425 ms |
| Moonshine base (v1) int8 | offline | 19.6% | 45/83 (54%) | 13.7% | ~230 ms |
| Kroko | streaming | 21.8% | 39/83 (47%) | 9.8% | ~41 ms (2 threads) |
| NeMo FastConformer 480 ms | streaming | 26.7% | 39/83 (47%) | 8.0% | ~130 ms (2 threads) |

- Offline models decode the whole clip after speech ends, so their wait grows with clip length (about 630 ms on the long easy-set clips). Thread counts differ between rows; the offline numbers are not like-for-like with the streaming ones.
- Kroko by category (hard set): music 11/30, contacts 5/15, places 1/5, brands 4/8, numbers 8/10, everyday 4/4, corrections 6/11. Typical failures are near-misses of a closed list ("opalate" for Opalite, "Pantropico" for Pantropiko, "Binnie" for BINI); some are hopeless ("usable to lack cruise" for Ysabel Dela Cruz).
- Moonshine v2 was not tested: no ONNX build in the sherpa-onnx catalogue, only v1.

## Things that did not work

| tried | result |
|---|---|
| `blank_penalty` 0.5 / 1.0 / 2.0 on Kroko, to stop dropped words | worse (9.8% -> 10.3-10.6% easy set) |
| `modified_beam_search` (4 paths) | 9.04% vs 9.82%: 3 words of 387, within noise |
| Hotwords on Kroko | not usable as shipped: no BPE model, sherpa rejects hand-made token sequences, phrase mode mis-segments; at score 6-12 it introduced new errors. **Works on Parakeet, see next section** |
| 100 Hz high-pass filter, neural denoising | no gain (our test); the research also says denoisers can hurt ASR |

## Hotwords on Parakeet (the biggest single gain so far)

An agy report claimed `modified_beam_search` and hotwords are broken for NeMo transducers in sherpa-onnx. **That is wrong**: both run on Parakeet-TDT v2 int8 in sherpa-onnx 1.13.8. What makes hotwords work is encoding names with `modeling_unit="bpe"` and a `bpe_vocab` file generated from the model's `tokens.txt` (`piece<TAB>-id/100`), with each name given in lowercase and Title Case (the model's vocabulary is cased). Plain lowercase phrases without that made some clips worse.

Hard set, hotword list of 317 entries (the ~70 test entities plus ~250 distractor names, places, artists and brands), `max_active_paths=4`:

| config | WER | entities found | p50 wait |
|---|---|---|---|
| greedy (baseline) | 8.3% | 60/83 (72%) | 371 ms |
| beam search, no hotwords | 8.8% | 60/83 (72%) | 421 ms |
| + hotwords score 0.5 | 6.1% | 68/83 (82%) | 410 ms |
| **+ hotwords score 1.0** | **4.7%** | **73/83 (88%)** | 425 ms |
| + hotwords score 1.5 | 6.4% | 75/83 (90%) | 424 ms |
| (answers-only list of ~70, score 1.0) | 3.4% | 75/83 (90%) | 434 ms |

Higher scores over-boost (WER 12.7% at 2.0, 24% at 3.0 with the small list). Easy-set check with the same big list: WER 3.6% before and after (the only difference was "cosy" vs "cozy"), so common English is not harmed. Cost: roughly +50 ms.

**Caveats:** the score was chosen on the same 59 clips; the list contains the right answers (a real contact list or library will not always); one speaker; hotword lists much larger than ~300 are untested; per-stream dynamic lists are untested on the offline recognizer. Treat 88% as an upper-middle estimate until it is re-measured on fresh sentences.

## Held-out check for the hotword result

`bench/stt_heldout.json` is 36 sentences written after the hotword list was frozen (`bench/hotwords_v1.txt`, 318 names, the exact list used above). Four kinds:

| kind | n | entities | what it tests |
|---|---|---|---|
| `heldout_in` | 10 | in the list, new sentences | does the gain survive unseen sentences |
| `heldout_out` | 12 | NOT in the list (Chappell Roan, Cup of Joe, Tuguegarao, Siargao, Goldilocks, Dilaw by Maki, ...) | do hotwords hurt names they do not know |
| `open_world` | 8 | none or generic | harm to ordinary questions, writing and how-to requests |
| `complex` | 6 | project, code and trip-planning requests | long multi-clause tasks a generic agent gets |

Record (about 5 minutes), then score. The script splits entity recall into in-list and out-of-list, and reports WER on clips with no entities, for greedy, beam search and three hotword scores:

```bash
uv run --group laptop python scripts/stt_compare.py record --items bench/stt_heldout.json --audio-dir bench/mic_heldout --no-noise
uv run --group laptop python scripts/stt_hotwords_eval.py --items bench/stt_heldout.json --audio-dir bench/mic_heldout --tag heldout
```

Do not edit `bench/hotwords_v1.txt` before scoring. Pass criteria suggested in advance: hotwords at score 1.0 keep out-of-list recall within a few points of greedy and add no WER on `open_world` clips; in-list recall stays clearly above greedy.

### Held-out result (36 clips recorded after the list was frozen; 2026-10-20)

| config | WER | in-list entities | out-of-list entities | p50 wait |
|---|---|---|---|---|
| greedy | 11.6% | 10/16 (62%) | 15/27 (56%) | 416 ms |
| beam search only | 11.6% | 9/16 (56%) | 15/27 (56%) | 442 ms |
| hotwords 0.5 | 10.0% | 12/16 (75%) | 15/27 (56%) | 442 ms |
| **hotwords 1.0** | **9.3%** | **13/16 (81%)** | 14/27 (52%) | 447 ms |
| hotwords 1.5 | 11.6% | 13/16 (81%) | 15/27 (56%) | 447 ms |

WER by kind, greedy -> hotwords 1.0: in-list 13.8% -> 5.0%; out-of-list 19.7% -> 22.7% (one extra wrong word, "Shake Keys"); open-world 6.9% -> 5.6%; complex 7.5% -> 6.5%.

- The pre-registered criteria hold: out-of-list recall did not drop beyond a one-entity wobble, open-world and complex clips did not get worse, in-list recall stayed above greedy. The in-list gain is real but small-sample (3 more entities out of 16): "Moira Dela Torre", "GCash" and "Jollibee" were fixed.
- Hotwords do **not** help names that are not in the list (Chappell Roan, Tuguegarao, Siargao, Goldilocks, Dilaw, Dumlao, Noli Me Tangere, Sagada/Banaue all still wrong). Expect the list to be the ceiling: ship contacts, the music library and the bounded place/brand lists.
- Score 1.0 is again the best; 1.5 gives back the WER gain.
- Also still wrong with hotwords: "Kristine" (heard "Christine"), "Taguig" ("Lagig"), "Grab" ("graft"). Spelling variants of a name that is in the list are exactly what the phonetic matcher and the "did you mean" step are for.
- Scorer fixes made during this run: "50,000" now equals "fifty thousand" and number spelling goes to 999,999 (two tests added). This lowered greedy's apparent WER disadvantage on two clips; the numbers above are after the fix.

## Parakeet confidence as an error detector

`stream.result.ys_log_probs` exists on the offline recognizer (one log-probability per token). Aggregating to word level (geometric mean) on the 59 hard clips: 408 words, 30 wrong. AUROC for separating correct from wrong words is **0.87** (0.85 using the minimum token probability). Flagging words with confidence below 0.9 catches 73% of wrong words but flags 16% of all words (33% precision); below 0.7 flags 3% of words at 50% precision but catches only 20%. So confidence is a useful gate for "check this word against the entity list", not a clean error detector on its own.

## Entity repair ladder (`src/lan_speech/transcript_repair.py`)

After Parakeet and hotwords, the transcript goes through a phonetic matcher and a five-rung ladder: **silent** replacement of a close, clear match; **implicit** replacement with the name echoed back (low-risk actions); **confirm** ("Did you mean X?", for plausible-but-unsafe matches and risky actions such as calls); **targeted** ("Which contact was that?" when the action needs a name and none was found); **reprompt**. Sound distance is a small rule-based grapheme-to-sound pass plus a weighted edit distance (vowels, b/p, d/t, f/p, v/b, s/z... are cheap substitutions); no model or G2P library. Thresholds are in `RepairConfig`. Names are `Entity(name, category)`, with categories in `bench/entities_v1.json` (contact, music, place, brand).

Evaluate (decodes once, caches in `out/stt_compare/repair-cache.json`; thresholds are tuned on the hard set and reported on held-out):

```bash
PYTHONPATH=src uv run --group laptop python scripts/stt_repair_eval.py [--refresh] [--verbose]
```

What it took to stop doing harm (first version: 18-31 false fixes and up to 11 broken names, WER up to 21%):

1. **Never touch part of a name that is already right.** "Dela" inside "Moira Dela Torre" was being turned into "Adele".
2. **Dictionary guard.** A span made only of ordinary English words (CMUdict, 126k words, plus a short list of command words) is never repaired. "dollars" is not "Dolores". Adds the `cmudict` package to the `laptop` dependency group only.
3. **Length gate** on the sound strings, so "angelo hello" cannot become "Angelo".
4. **Slot awareness.** Match only against names of the kind the agent expects (contact for "call", music for "play"). This removed the remaining false fixes.

Result with all four (hotword decode as input; hard = tuning set with the answers in the list, held-out = names mostly not in the list):

| mode | set | entities after hotwords | + auto repair | + asks counted | false fixes | names broken | WER |
|---|---|---|---|---|---|---|---|
| global list | hard | 88% | 89% | 90% | 2 | 1 | 4.7% -> 4.4% |
| global list | held-out | 63% | 60% | 63% | 2-3 | 1 | 9.3% -> 10.0% |
| **slot-aware** | hard | 88% | 89% | 92% | 1 | 1 | 4.7% -> 4.4% |
| **slot-aware** | held-out | 63% | 63% | 65% | **0** | **0** | **9.3% -> 9.3%** |

Held-out, slot-aware: 4 "did you mean" questions (1 offered the right name) and 9 "which <slot>" questions, 7 of them on clips where the name was in fact mis-heard. The sweep on the hard set preferred silent 0.08, implicit 0.20, confirm 0.40, margin 0.04 over the defaults in `RepairConfig` (0.16 / 0.26 / 0.38 / 0.06); both gave the same held-out result, so the defaults were left as they are.

**Read this honestly:**
- **The automatic fix adds little once hotwords are on** (+2 entities hard, 0 held-out). Hotwords already recover most of what sound matching can; what is left is mostly names not in the list, which no list-based matcher can fix. The earlier prototype's 71-80% was an unguarded matcher against a list containing the answers and should be ignored.
- **The ladder's value is safety, not recall:** with slot awareness it never breaks a correct transcript, and it knows when a required name was not found, so the agent can ask instead of calling the wrong person.
- **Decoder confidence added nothing** (identical results with and without a greedy-pass confidence per word), so the design needs no second decode. Note that with hotwords on, `ys_log_probs` of boosted tokens are positive (the bonus is baked in), so confidence from the hotword decode is not usable anyway.
- Fragile spots: `COMMON_WORDS`/CMUdict also contains many names ("Binnie" is a CMU entry), so those are protected from repair; slot labels come from the caller and were assigned by hand for the test clips (`slot` field in the clip scripts); risk (high for call/text/message/send) is inferred from the first word; the sample is 95 clips from one speaker.

## Speed experiments on synthetic speech (no recordings needed)

`scripts/make_synth_clips.py` renders the clip scripts with Kokoro (95 clips, mean 2.5 s, two voices) and `scripts/stt_latency_bench.py` times Parakeet on them. **Speed only**: clean synthetic speech says nothing reliable about accuracy. Laptop: i5-1145G7, on AC, governor `powersave`; main run with profile `balanced` (EPP `balance_performance`), repeated with `scripts/power_mode.sh max` (profile `performance`, EPP `performance`; restored to `balanced` afterwards). Padding in all rows except the padding table is 0.5 s lead + 1.5 s tail.

**Threads** (decode p50 / p95 ms): 1 thread 549 / 825 greedy, 604 / 870 hotwords; **2 threads 406 / 585, 425 / 619**; **4 threads 397 / 536, 444 / 618**; 8 threads 656 / 835, 764 / 1044. Two threads is as fast as four, and 8 (all hyperthreads) is slower. Hotwords add roughly 20-50 ms.

**Length:** decode ms is about 279 + 72 x (seconds of audio fed, padding included). A 2 s command with 2 s of padding costs ~450 ms; the same command with no padding ~300 ms.

**Padding** (4 threads, hotwords): 0.5 / 1.5 s lead / tail: 451 ms; 0.5 / 0.5: 378; 0.2 / 0.5: 363; 0 / 0.3: 335; 0 / 0: 298. Shorter padding changes the text on 27-39 of 95 clips, but WER against the script did not rise (4.7-6.5% vs 5.8%), so on clean synthetic speech Parakeet does not need the long padding. Kroko and NeMo dropped edge words without it (see `STT-COMPARE.md`); whether Parakeet does on real speech is **untested**.

**Early decode** (decode as soon as the VAD sees a 0.2 s pause, before the turn is confirmed at ~0.5 s): the words match the 0.5 s-tail decode on 89 of 95 clips (94%; 82 of 95 after 0.1 s). Decode takes ~340 ms, so starting it at the first pause finishes about when the turn is confirmed. The 6 differences are the case to design around (re-decode if the user keeps talking or if the last word looks uncertain). Real speech endings differ from synthetic ones, so treat 94% as an upper estimate.

**Sharing the CPU with TTS** (Supertonic, 4 steps, 2 threads, running back to back): Parakeet is **1.5x slower with 2 threads (435 -> 666 ms p50) and 1.65x with 4 (448 -> 739 ms)**, and a TTS call goes from 957 ms to 1315 ms (2 STT threads) or 1740 ms (4). So give STT 2 threads: it is as fast alone, and it hurts TTS less. This matters in full duplex, when the user speaks over a reply that is still being synthesised.

**Power profile `performance` vs `balanced`** (same 95 clips, same script, run back to back): small and mostly inside run-to-run noise (about 10%). Where it shows: 4 threads, hotwords, 0.5 / 1.5 s padding 444 -> 390 ms p50 (greedy 397 -> 361); 2 threads 425 -> 400; early decode at a 0.2 s pause 333 -> 299 ms; no padding 298 -> 299 ms (no change). It did not help under TTS load (STT still 1.69x slower; 2 threads 666 -> 679 ms, TTS call 1315 -> 1375 ms) and 8 threads stayed slow (756 ms). Average clock barely moved (2856 -> 2926 MHz, the governor stays `powersave`), so the limit is not the profile. Best case on this laptop: about 0.2 s VAD pause + 0.30 s decode = 0.5 s before the transcript reaches the LLM.

**What this gives for the latency budget on this laptop:** VAD pause (0.2 s) + decode (~0.34 s with short padding and the early start) is about 0.5 s before the transcript reaches the LLM, against ~0.25 s for Kroko. The 500 ms to first audio target is therefore not reachable by speech recognition alone; it needs the masking tricks (acknowledgement sound, LLM prefill on the Kroko partial) or a faster host.

## Network baselines from this laptop (not the demo network)

`scripts/net_probe.py cloud` (TCP connect and TLS handshake only, no audio), 25 attempts, laptop on its normal Wi-Fi (Pi power save was on):

| endpoint | connect p50 / p95 ms | TLS p50 ms |
|---|---|---|
| api.deepgram.com | 238-261 / 313-337 | 242-278 |
| api.au.deepgram.com (Australia) | 245 / 336 | 258 |
| api.eu.deepgram.com | 231 / 272 | 244 |
| ec2.ap-southeast-1.amazonaws.com (Singapore) | 54 / 254 | 69 |
| api.groq.com | 10 / 22 | 31 |
| api.cartesia.ai | 22 / 32 | 40 |
| api.anthropic.com | 14 / 100 | 32 |

Reading it: Deepgram is about a quarter-second round trip from here on every endpoint it offers (US, EU, Australia; there is no Singapore one), so a streamed transcript cannot arrive sooner than one such round trip after the audio, before any server processing and before the extra delay of mobile data. Groq, Cartesia and Anthropic answer from a nearby edge (10-22 ms), but a fast connect to a CDN edge does not mean fast inference. This is the laptop's own connection; the numbers that decide the demo are the ones from the Pi on the phone's hotspot.

## Running the tests on the actual devices

**Hotspot reachability and jitter (Pi <-> laptop), 5 minutes.** Put both on the phone hotspot, then:

```bash
# laptop
python3 scripts/net_probe.py serve --port 9099
# Pi (use the laptop's hotspot IP: `ip -brief addr` on the laptop)
python3 scripts/net_probe.py lan --host <laptop-ip> --port 9099 --json lan.json
```

It prints UNREACHABLE if the phone isolates clients; otherwise round trips and the delay of a 256 kbps audio-like stream (20 ms frames). Also run `python3 scripts/net_probe.py cloud` on the Pi on the hotspot to get the real cloud round trips and jitter. Copy `net_probe.py` alone; it is stdlib-only.

**Phone speed and thermals (Termux), about 45 minutes the first time.**

```bash
pkg install python python-numpy
pip install sherpa-onnx
# copy to the phone: models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8 (4 files),
# out/synth (the WAVs), scripts/phone_bench.py
python phone_bench.py --model ~/parakeet-v2-int8 --clips ~/synth --threads 4 --minutes 10
```

It prints decode time, real-time factor and temperatures per round; a rising time per round means throttling. For a fair comparison run the same command on the laptop. Drop the phone as a host if the first round is already slower than the laptop's ~340 ms, or if it slows by more than ~1.5x over ten minutes.

## Hosting and cost

- Deepgram Flux English: $0.39/h pay-as-you-go, $200 free credit. Two hours of testing costs under $1.
- Renting a Singapore GPU (AWS g4dn.xlarge $0.74/h on-demand, ~$0.29/h spot; Vultr A16 $0.51/h) costs about the same per hour as Flux plus setup time, so renting does not beat hosted STT at this volume. Parakeet already runs at RTF ~0.08 on laptop CPU, so a CPU-only box would also work (not priced).
- Renting does give reproducibility (pinned container) and a primary that behaves like the laptop backup.
- Unmeasured: phone (POCO X7 Pro) as a host. The agy report extrapolated CPU parity with the laptop and thermal throttling of 25-40%; none of it is measured. Parakeet 0.6B is a heavier test than Kroko.

## Network resilience (what other systems do)

Three agy reports (`AGY-NET-*.md`, in `.scratch/speech-hosting/`) covered shipped assistants, papers and engineering practice. Convergent findings, in order of value for us:

1. **Keep audio off the cellular link.** Pi streams to a nearby host over the LAN; only text crosses mobile data (Wyoming, Willow, Alexa local control).
2. **Mask latency locally.** Pre-recorded acknowledgement or earcon played from the Pi when the VAD ends. No model, ~15 lines.
3. **Failover on latency, not just errors**, with a circuit breaker and hysteresis (LiveKit fallback adapters, Vapi fallbackPlan).
4. **Start the LLM on partial text** (Kroko), confirm with Parakeet: the classic two-pass shape. Prefill only; do not speak early.
5. **Never go silent**: canned "can't reach the server" clip; replay mode for a dead venue.
6. **Cheap fixes**: Pi `iw dev wlan0 set power_save off`, Opus in-band FEC if audio ever crosses WAN, warm connections, prompt caching, TTS prebuffer 60-100 ms.

Spot checks of the papers: ConvFill (2511.07397) exists but gives no latency number in the abstract (the report's "sub-200 ms" is unsupported); Endpoint Anticipation (2606.13450) confirms a 505 ms average saving at +28.4% speculative compute; Continuous-Time Agents (2609.17416) confirms 19% overall; ASTA (2512.12769) routes on CPU, temperature and network latency but its quoted thresholds are not in the abstract.

## Open questions and risks

- **Hotspot reachability is unverified and the reports contradict each other.** If the phone isolates clients, the Pi cannot reach the laptop over the hotspot. Test: serve a file from the laptop, `curl` it from the Pi. Fixes: Ethernet cable Pi-laptop, or a travel router.
- Single speaker, 59 clips: a 2-point difference is noise. A second speaker is needed to say anything about accents.
- The agy report on improving Parakeet (`AGY-PARAKEET-IMPROVE.md`) is wrong about hotwords (see above) and its other numbers (LoRA gains, A100 hours, WER thresholds) are unmeasured background knowledge. The Pareto/repair report (`AGY-PARETO-REPAIR.md`) gives usage shares that are the model's own synthesis, not published figures.
- Parakeet adds ~390 ms after speech ends; it is not streaming in sherpa-onnx. Early decode on a VAD pause is an untested idea.
- The agy reports tag most claims "verified in snippet"; treat that tag as unreliable.

## Decision rule (proposed)

Phone fast enough and reachable -> Parakeet on the phone, Kroko as instant fallback. Laptop reachable, phone not -> laptop. Neither -> cloud, with measured round-trip numbers deciding the risk. Name matching stays in the agent in every case. Fine-tuning (NeMo/Parakeet on the HPC cluster) only if a second speaker shows a large accent gap after the cheap fixes.

## Reproducibility notes

- **Parakeet is installable from git.** `uv run python scripts/fetch_laptop_models.py` downloads Parakeet-TDT 0.6B v2 int8 (~660 MB) into `models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/`, from a pinned Hugging Face commit (`1ab9323...`) with SHA-256 checks (`--check` verifies only; `--server-only` skips it). The model files themselves are git-ignored (`models/`). Moonshine and Parakeet v3 were only tried by hand and are not in the manifest.
- **Scoring is in `scripts/`:** `stt_compare.py` (record and score streaming models, now with `--items` JSON scripts and digit/thousands normalisation) and `stt_hotwords_eval.py` (Parakeet with and without hotwords, split by in-list and out-of-list entities). Tests: `tests/test_stt_compare.py`, `tests/lan_speech/test_models.py`.
- **Fixed inputs:** `bench/stt_hard.json`, `bench/stt_heldout.json` (clip scripts with entities) and `bench/hotwords_v1.txt` (the frozen 318-name list). Recordings (`bench/mic_hard/`, `bench/mic_heldout/`) are the owner's voice and are git-ignored; a second speaker would need their own.
- **Not yet clean:** the one-off experiment scripts behind the Kroko, Moonshine and name-matcher numbers are working copies in `.scratch/speech-hosting/scripts/` with absolute scratch paths. Latency numbers depend on power state and thermals; run with the same power profile and record it.
- **Raw notes**, per-config numbers and corrections to the agy reports: `.scratch/speech-hosting/VERIFICATION.md`.
