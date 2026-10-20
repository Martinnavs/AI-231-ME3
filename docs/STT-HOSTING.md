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

Later changes (found while trying it on the live server): a leading or trailing ordinary word must sound like the name's own first or last word ("call" in "call rowana" is not part of the name, but "The" in "The Weeknd" is); an ordinary word that sounds *identical* to a listed name is that name respelled ("Christine" for Kristine); weak guesses (distance above 0.30) are no longer offered as "did you mean" and the slot question is asked instead; the slot may combine kinds ("brand+place" for "book a Grab to Makati"). The slot question reads "Which artist or song was that?".

Result with all of it (hotword decode as input; slot-aware, no confidence, default thresholds; hard = tuning set with the answers in the list, held-out = names mostly not in the list):

| set | entities after hotwords | + auto repair | + asks counted | false fixes | names broken | WER |
|---|---|---|---|---|---|---|
| hard | 88% | 93% | 94% | 1 | 1 | 4.7% -> 3.4% |
| held-out | 63% | 65% | 67% | **0** | **0** | 9.3% -> 9.0% |

Held-out: 1 "did you mean" (right name), 11 "which <slot>" questions, 8 of them on clips where the name really was mis-heard. The sweep's best on the hard set (silent 0.08, implicit 0.20, confirm 0.40, margin 0.04) gave the same held-out recall with more, mostly wrong, guesses, so the defaults (0.16 / 0.26 / 0.30 / 0.06) were kept. The eval scores one slot per clip; multi-slot ("brand+place") is only exercised by the unit tests and the live tryout.

**Read this honestly:**
- **The automatic fix adds a few points once hotwords are on** (+5 entities hard, +1 held-out). Hotwords already recover most of what sound matching can; what is left is mostly names not in the list, which no list-based matcher can fix. The earlier prototype's 71-80% was an unguarded matcher against a list containing the answers and should be ignored.
- **The ladder's value is safety, not recall:** with slot awareness it never breaks a correct transcript, and it knows when a required name was not found, so the agent can ask instead of calling the wrong person.
- **Decoder confidence added nothing** (identical results with and without a greedy-pass confidence per word), so the design needs no second decode. Note that with hotwords on, `ys_log_probs` of boosted tokens are positive (the bonus is baked in), so confidence from the hotword decode is not usable anyway.
- Fragile spots: `COMMON_WORDS`/CMUdict also contains many names ("Binnie" is a CMU entry), so those are protected from repair; slot labels come from the caller and were assigned by hand for the test clips (`slot` field in the clip scripts); risk (high for call/text/message/send) is inferred from the first word; the sample is 95 clips from one speaker.

## Running the Parakeet engine (`--stt-model parakeet`)

**What it is:** a hybrid. Kroko streams interim text and detects pauses, as before. When a turn ends (Kroko's endpoint, or a `finalize` message), the buffered audio is re-decoded by Parakeet with hotwords from a names file, and the text goes through the repair ladder. Interim transcripts are therefore Kroko's (weaker); finals are Parakeet's. Kroko stays the default engine; nothing changes unless you pass the flag.

```bash
uv run python scripts/fetch_laptop_models.py          # once; also fetches Parakeet (~660 MB)
uv run --group laptop python -m lan_speech --stt-model parakeet --insecure-loopback
uv run --group laptop python scripts/try_stt.py       # in another terminal: live microphone
uv run --group laptop python scripts/try_stt.py --wav out/synth/stt_heldout-06.wav   # a file
```

`try_stt.py` acts like the Pi: it streams audio and, after `--vad-hold` seconds of quiet (default 0.5), sends `finalize`. Its pause detector is adaptive: the first second only measures the room, then speech is anything well above the noise floor, so steady hum or a fan does not count as speech (an earlier fixed bar did, see below). It prints Kroko's interim text, then the final line (with what Kroko had streamed when Parakeet's version differs, which is the improvement you see between the interim and final text), the raw Parakeet text if repair changed it, whether name repair ran (it only runs for requests with a name list: call/text, play, book/order, weather-style, or `--slot`; open-ended questions skip it by design, the Parakeet re-decode still happens), and who ended the turn: this client after the pause (with the server's answer time), or the server's own detector (no timing claim, since it ends turns late). Useful flags: `--slot contact --risk high` (hints the agent would send), `--save DIR` (keep each turn's audio and result for offline scoring), `--levels` (live microphone level and noise floor), `--vad-hold 0` (let the server decide), `--device N` (microphone).

**Server flags:** `--entities config/entities.json` (default), `--parakeet-threads 2`, `--hotword-score 1.0` (1.5 or more hurts; 0.5 is gentler), `--pad-lead 0.5`, `--pad-tail 1.0`, `--early-decode-ms 0` (200 starts Parakeet at the first pause; see below), `--no-repair` (hotwords only), `--endpoint-silence`. Startup takes about 30 s (three models).

**Your names:** `config/entities.json` is the list that both biases Parakeet and feeds the repair step: `{"Display Name": "category"}` with categories `music`, `contact`, `place`, `brand` (anything else is used for biasing only). It ships with the 318 names used in the experiments (artists, Filipino names, places, brands, number phrases). Replace it with your real contacts and library; it is read at startup. Use proper casing for the display name (`GCash`, `BINI`), since that is what replaces a mis-heard word. A plain JSON list of names also works (biasing only).

**Protocol additions** (both optional, omitted when unset, so existing clients are unaffected): the `start` message may carry `"slot": "contact"` and `"risk": "high"`; a final `transcript` may carry `"raw_text"` (Parakeet before repair) and `"repair": {"rung", "question", "echo", "slot", "risk", "matches": [{"heard", "entity", "distance", "margin", "rung"}]}`. The final `text` already has silent and echoed replacements applied; for `confirm` and `targeted` rungs the text is left as heard and `repair.question` is what to say instead of acting. If the client sends no `slot`, the server guesses it from the first verb (call/text/message = contact and high risk; play/put on = music; book/order/buy = brand+place; weather/rain/far = place); with no guess no repair is applied. A client that knows the intent should send it.

**What to expect, and what is not verified:**
- On synthetic clips through the real server a final arrives about 0.9-1.1 s after the last speech with `--vad-hold 0.5` (0.6 s client pause + 0.3-0.5 s server) on a laptop busy with a video call. Without a client-side pause the server's own endpoint is slow: Kroko ended turns 1.6-2.3 s after speech even with `--endpoint-silence 0.7` (3.4 s at the 1.2 s default) on synthetic digital silence; real microphone noise may behave differently. In the demo the Pi's VAD should send `finalize`.
- Before Parakeet decodes, the buffered turn is trimmed (0.3 s before, 0.4 s after the speech) and then padded with digital silence, 0.5 s before and 1.0 s after (`--pad-lead`, `--pad-tail`). The padding matters: see the table below.
- One slot per turn (or an explicit combination). A sentence naming a contact and a place only repairs the kind its first verb implies.
- Names the dictionary knows (CMUdict has many first names) are protected from repair unless they sound identical to a listed name.
- Hotwords are fixed at startup; per-request lists would need a different mechanism.
- The CPU is shared: Kroko (2 threads) + Parakeet (2) + Supertonic (2) on 8 logical cores. Decoding while TTS runs was measured at 1.5-1.65x slower.

### First live microphone tryout (2026-10-20, owner, 14 turns, no ground truth)

Open-domain talk transcribed with punctuation and mostly well: "Do you know what gen alpha means when they say six seven?", "Can you tell me the biography of Frederick Nietzsche?", "Do you know what the phenomenology of spirit is?", "How many dictators does it take to turn an empire into a union of ruinous states?". Two attempts at the woodchuck tongue twister came out garbled both times (to be expected for a tongue twister). "John Dewey II times two plus one." looks wrong but what was said is not known. Digits and words were not consistent ("square root of 2" vs "of two"); the LLM can absorb that. No name list applied to any of these (none were call/play/book requests), as designed.

Two things in that run were artifacts of the test tool, now fixed: the delays it printed ("arrived 5-28 ms after the last speech") were meaningless because a fixed loudness bar counted room noise as speech, so the client never saw a pause and the turns were ended by Kroko's slow endpoint instead; and one line showed a negative client pause when sound arrived after `finalize` was sent. Also visible: "The phenomenology of spirit is." was cut off as a fragment and then repeated in full, so a 0.5 s pause ends a turn in the middle of a thinking pause; real use will want a longer hold or smarter turn detection, and the right value has not been measured. On a synthetic 14-word request with a natural pause in the middle, a 0.5 s hold cut the sentence after the first half; a 1.0 s hold kept it all but the final arrived about 1.85 s after the end of speech (0.5 s hold: about 0.9 s). The test client now warns when a pause ends a WAV clip early.

### Padding, measured on the labelled clips (2026-10-20)

The first version of the server trimmed the silence but added no zeros, a different treatment from the one behind every published number (0.5 s before and 1.5 s after). On the 95 labelled clips (hard + held-out, your recordings, hotwords on) the difference was real:

| preprocessing | WER hard | WER held-out | entities held-out | decode p50 |
|---|---|---|---|---|
| as evaluated so far (no trim, 0.5 + 1.5 s zeros) | 4.7% | 9.3% | 27/43 | 450 ms |
| first server version (trim 0.3/0.4, no zeros) | 5.6% | 11.6% | 27/43 | 302 ms |
| trim, 0.3 + 0.5 s zeros | 5.4% | 10.0% | 27/43 | 353 ms |
| trim, 0.5 + 0.5 s zeros | 6.1% | 8.4% | 31/43 | 363 ms |
| **trim, 0.5 + 1.0 s zeros (now the default)** | **4.9%** | **8.7%** | **30/43** | 395 ms |
| trim, 0.5 + 1.5 s zeros | 4.9% | 9.3% | 30/43 | 417 ms |
| no trim, no zeros | 6.6% | 12.2% | 26/43 | 314 ms |

Differences between the padded rows are within the noise of 95 clips from one speaker; the clear result is that some zero padding is needed and that trimming first saves about 55 ms. Script: `scripts/stt_padding_eval.py`.

### 94 turns of free live speech (unlabelled; `--save out/tries`)

You read song lyrics, book openings and tongue twisters into the microphone (5.5 min). There is no transcript of what was said, so none of this is a WER; `scripts/analyze_tries.py` reports what can be measured without one.

- **Room:** noise floor median -36 dBFS (worst turn -28), speech median -21 dBFS, so about 14 dB of signal to noise, 4 dB worse than the room of the labelled clips (-40). No clipping.
- **Fragments:** 36 of 94 turns end open, start in lower case or are three words or fewer, and about 20 are plainly cut in the middle of a sentence ("heartbreak of the", "where ice", "His father took"). The 0.5 s pause ended turns inside sentences, as the book and lyric readings paused every few words. This is the hold-time trade-off above, not a recognition error.
- **Hotwords on open speech:** 22 of 94 turns changed against plain decoding. Most changes are plausible fixes ("pile driver waltz", "I can promise that things would be broken"), but a listed name appeared where it should not in 4: "turned to **Francis**" (the book says "face"), "Colonel Aureliano Buendia" came out with **Arthur** and **London** appended or substituted, and a 0.7 s noise-only turn became "**Thirty.**". So the earlier finding that hotwords did no harm on held-out speech does not extend to hard, noisy open reading: they can insert a wrong listed name. A lower `--hotword-score` (0.5 gave most of the gain on the labelled clips) is the obvious thing to try, but cannot be judged without a transcript.
- **Padding sensitivity:** on these turns every extra bit of zero padding (0 -> 0.2 -> 0.5 -> 1.0 -> 1.5 s) changed 6-15% of the words, with no plateau, so unlabelled turns cannot pick the padding; that was settled on the labelled clips above. An earlier reading of this data ("trimming clips the ends of words") was wrong: the trimmer removes almost nothing from these buffers; the differences came from zero padding.
- **Data problem (my bug, fixed):** for 16 of the 94 saved turns the audio does not match the saved text (for example a 0.7 s clip next to a two-sentence transcript), because the test client paired a final with a stale audio snapshot when the server's own end-of-turn raced the client's `finalize`. Each final is now saved with all the audio since the previous final. Use only newer saves for scoring.
- **Slot guesser:** "Call me Ishmael" asked "Which contact was that?" because "call" looked like a phone call. "call/text/send" followed by a pronoun (me, it, this, them...) now gets no slot.

To turn free reading into a measurement, write down what you said for a subset: `scripts/analyze_tries.py` and `scripts/stt_padding_eval.py` can then score any setting (hold time, padding, hotword score) on speech from this room.

### Live session with a transcript (2026-10-20, 25 turns, 291 words, owner reading books and lyrics)

The first measurement on live speech with the true text (`out/tries/truth.tsv`, scored by `scripts/score_tries.py`). Server on `--stt-model parakeet` with the padding fix, `try_stt.py` with a 0.5 s pause hold, same noisy room as before (about 14 dB signal to noise).

**The live server's final transcripts: 13 word errors in 291 words = 4.5% WER.** Reading each error: one is the scorer not expanding a contraction ("would've" vs "would have"), one is probably a word that was actually said ("acknowledged that"), and one has no cause in the audio (turn 16 should start "Planning to go to"; the recording contains only "Sagada"). Without those three the rate is about 3.4%. The real recognition errors:

| said | heard | why it is plausible |
|---|---|---|
| Start walking uphill to **train** your lungs | to **drain** | voicing of the first consonant (an accent pattern: t -> d) |
| Planning to go to **Sagada** | **Zagada** | not in the name list; add the places you care about to `config/entities.json` |
| I'd say **Quezon** was an okay zone for the case on **Kay's son** | **Kazan** ... **Case Sun** | a deliberate homophone test; "Quezon" is in the list and still lost |
| Damn it **we** jamming | **with** jamming | |

Replaying the saved audio under other settings (to choose a setting, not to measure the live system; see the bug below):

| setting | word errors | WER |
|---|---|---|
| hotwords 1.0, pad 0.5 + 1.0 s (the server default) | 16 | 5.5% |
| hotwords 0.5 | 16 | 5.5% |
| hotwords 1.0 with no padding / pad 0.5 + 1.5 s | 16 / 16 | 5.5% |
| no hotwords, beam search | 18 | 6.2% |
| no hotwords, greedy | 21 | 7.2% |

So hotwords help a little even on open reading (names like GCash and David Foster Wallace), a hotword score of 0.5 did as well as 1.0 on this text, and padding made no difference here. With 291 words these gaps are small.

**Two corrections to my own earlier analysis, found by this session:**

- The saved audio was missing the start of most turns. My `--save` code cleared its buffer when a final arrived and so filed the half second you spoke while the server was still answering `finalize` under the *previous* turn. The server itself received that audio correctly (the live text has "Things are better if I stay", "Every unhappy family"; the saved WAVs begin mid-word). Replaying the saved WAVs therefore overstates errors (5.5% vs 4.5% live), and a "pre-roll" experiment I ran on them (0.4 s of the previous turn put in front: 5.5% -> 5.2%, 1.0 s: 4.5%, 1.5 s: 11.3%) was measuring that bug, not a server problem. Fixed: audio up to the moment `finalize` was sent is saved with that final, the rest goes to the next turn (`take_turn_audio`). Earlier sessions' WAVs have this defect.
- The slot guesser asked "Which place was that?" after a statement beginning "Far out in the uncharted backwaters ...", because "far" looked like a place cue. A bare "far" or "near" is no longer a cue ("how far" is), and an utterance longer than 14 words is never treated as a command.

### Latency of the live path, measured on that session (2026-10-20)

`scripts/stt_finalize_latency.py` builds the server's engine with its default settings (Kroko 2 threads, Parakeet 2 threads with hotwords, padding 0.5 s + 1.0 s, repair on), streams each saved turn into it as the server would, and times `finalize` until the final transcript exists. Laptop on AC, `balanced` profile, a browser using some CPU in the background. 24 measured finalizes of your 25 turns (these are long: mean 5.6 s, because you read lines; commands are typically 2-3 s).

| part | p50 | p95 | max |
|---|---|---|---|
| **finalize total (what the server adds after the pause)** | **722 ms** | 1070 ms | 1117 ms |
| Parakeet decode of the padded audio | 662 ms | 1016 ms | 1044 ms |
| Kroko finishing the turn + name repair | 47 ms | 79 ms | 120 ms |

By length of the turn: under 3 s, p50 460 ms (max 558); 3-5 s, 598 ms; 5-8 s, 829 ms; over 8 s, 979 ms. Parakeet is an offline model, so the whole turn is decoded after it ends and the cost grows with the length of what was said (about 70 ms per second of audio plus about 280 ms fixed, which is also what the synthetic benchmark found).

What a user waits, from the end of their speech to a transcript the agent can use: the client's pause (0.5 s here) plus the server's time. For a 2-3 s command that is about 0.5 + 0.46 = **1.0 s**; for a 5-8 s sentence about 0.5 + 0.83 = **1.3 s**; for 8 s and over about 1.5 s. The LLM's first token and the first audio of the reply come on top of that (TTS first audio was measured at about 320 ms with Supertonic alone; the LLM was not measured here). The target of about 500 ms to first audio is not met by speech recognition alone, and these figures do not include the 1.5-1.65x slowdown measured when TTS runs on the same CPU.

Levers, with what is known: **start the decode at the first short pause** (0.2 s) and discard it if the user keeps talking (on synthetic clips the words matched the 0.5 s-tail decode on 94% of clips; not built); **a shorter pause hold** (0.5 s ended turns mid-sentence when reading in chunks, but a command is a single burst); **a faster host for Parakeet** (the phone benchmark is the open question; a GPU would cut the decode to tens of milliseconds); **give the LLM Kroko's streaming text early** and let Parakeet correct it (the fast partial is available while the user speaks; not built). Smaller: a 1.0 s tail pad costs about 70 ms against 0.5 s but the shorter pad was worse on the hard set (6.1% vs 4.9%); 2 and 4 threads are equal; the `performance` power profile gains about 10%.

### Early decode at the first pause (built, off by default: `--early-decode-ms 200`)

The server watches the audio for a short pause (200 ms of quiet after speech, relative to the room's noise floor, which is carried from turn to turn). When it sees one it starts the Parakeet decode in the background. If the speaker carries on, or anything rises above 1.5x the noise floor (the soft end of a word), the result is thrown away; if the turn ends instead (the client's `finalize`, 0.5 s later), the answer is already computed or partly computed. Code: `lan_speech/pause.py` (detector), `HybridSTTStream` (speculation).

**What it saves** (`stt_finalize_latency.py --early 0.2`, last 0.5 s of each turn fed in real time, time from `finalize` to the final transcript): median **659 ms -> 375 ms** (p95 967 -> 750); turns under 3 s, 457 -> **100 ms** (max 528); 3-5 s, 586 -> 433; 5-8 s, 792 -> 367; over 8 s, 923 -> 699. On a command that is roughly the whole server wait.

**What it can cost** (`stt_early_eval.py`, labelled clips, early decode vs full-clip decode, both scored against what was said):

| set | clips that got an early start | WER full | WER early | words changed |
|---|---|---|---|---|
| hard (short commands) | 16 of 59 | 4.9% | **7.4%** | 10 of 408 |
| held-out | 8 of 36 | 8.7% | 8.7% | 1 of 311 |
| live session (25 turns) | 21 of 25 | 5.5% | 5.5% | 0 of 291 |

So on open reading and on the held-out sentences it changed (almost) nothing, but on short commands it cost 2.5 points of WER. Parakeet's words depend on the audio around the speech (see the padding results), and an early decode sees 0.2 s of trailing room noise where the full decode sees 0.4 s or more; on 16 command clips that was enough to change about one word in ten. A first version without the "soft end of a word" cancellation did far worse (hard set 18.4%): an energy-based pause is declared inside quiet word endings, so the decode missed them. Any rise above 1.5x the noise floor after the pause now cancels the early decode. A 0.3 s pause was no better (12 changed words on the hard set).

**Through the real server** (websocket, `--early-decode-ms 200`, four synthetic clips twice, 0.5 s client pause): the server answered 54-172 ms after `finalize` instead of 300-660 ms, with the same transcripts; about 0.55-0.67 s from the end of speech to the final text instead of 0.9-1.1 s.

**Why it is off:** the user's priority is transcript accuracy, and this trades 0.3 s for a real accuracy risk on exactly the short commands that are the common case. Try it with `--early-decode-ms 200` and judge on your own commands; a safer variant (hand the LLM the early text as provisional and send a correction if the verified decode differs) needs client support and is not built. Other caveats: the first turn after a restart has no noise estimate, so it has no early start; speculative decodes that get cancelled still burn CPU, which competes with Kroko and with TTS; all numbers are one speaker and one laptop.

### Which early-start approach? (precedent: ticket 12 / design review §3)

The project already decided how to start early: **ticket 12, "speculative LLM start"** (`config/agent.toml`: `speculative_llm = false`, `speculative_silence_ms = 180`; design review §3, "start the LLM early"). When an interim transcript looks like a complete clause and VAD silence has lasted 180 ms, start `respond()` but **hold its audio**; if the final transcript matches the speculative text (lowercase, punctuation stripped) release it, otherwise cancel (`LLM_CANCELLED`, reason `text_changed`) and start fresh; log the cancellation rate, because cancelled requests are still billed for their input tokens. The research notes support the shape (Endpoint Anticipation, arXiv 2606.13450: -505 ms average at +28.4% speculative compute; the practice report: speculative *prefill and tool intent only*, never speak early; two-pass ASR as the standard way to pair a fast first pass with a verified second one). The pipeline that would hold and release audio (`pipeline/speculative.py`) is not built yet.

That is a different approach from `--early-decode-ms` above, which uses the early Parakeet result *as* the final. Measured with `scripts/stt_provisional_eval.py` (turns where a 0.2 s pause started a speculative path; "match" = the provisional text, normalised, is word for word the verified final):

| provisional text from | hard (short commands) | held-out | live session |
|---|---|---|---|
| Kroko's interim text at the pause | 0 of 16 match | 0 of 8 | 0 of 21 |
| **Parakeet early decode** | **12 of 16 (75%)** | **7 of 8 (88%)** | **21 of 21 (100%)** |

Kroko's streaming text at the pause has not caught up with the speech yet (word error 26-67% against what was said), so it can never confirm; it is not a usable provisional source. Parakeet's early decode confirms in 40 of 45 turns (89%); the 5 cancellations are all on short commands and held-out sentences (25% and 12% there). Coverage: only turns where the pause detector fires get an early start (22-27% of the labelled clips, which have short recorded tails; 84% of the live session turns, which carry the client's full 0.5 s hold).

| approach | speed (a 2-3 s command) | accuracy | cost |
|---|---|---|---|
| nothing (verified decode after the pause) | transcript at about 0.96 s after speech ends | 4.9% hard / 8.7% held-out | none |
| **early result used as the final** (`--early-decode-ms`) | about 0.7 s (saves about 0.25-0.3 s) | hard set 7.4%: wrong text is passed on unverified | none |
| **speculative LLM start on Parakeet's early text, verified by the full decode** (ticket 12) | LLM starts about 0.3 s earlier; first audio about 0.3 s sooner on the 89% that confirm | unchanged (the verified text decides); cancelled turns cost the baseline | LLM input tokens on cancelled turns (about 11% overall, 25% on short commands) and a second Parakeet decode |
| speculative LLM start on Kroko's interim text | none (never confirms) | unchanged | wasted tokens on every turn |

Timings are the measured STT figures with the LLM (about 0.35 s, estimated, not measured) and TTS first audio (about 0.3 s) added; they assume the speculative LLM starts when the early decode finishes.

**Reading:** the two useful approaches save about the same time. The precedent approach keeps accuracy (the full decode still decides) at the price of tokens and a pipeline that does not exist yet; the shortcut costs nothing to run but passes slightly worse text on roughly a tenth of turns (a quarter on short commands). Given that accuracy of the text the agent acts on is the stated priority, the precedent is the one to build, with the early Parakeet decode as the provisional source and the existing verified path as the confirmation.

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
