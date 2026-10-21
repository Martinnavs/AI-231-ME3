# How assistants and frameworks handle echo and barge-in (research digest)

Source: an agy web-search run on 2026-10-21 (`.scratch/speech-hosting/AGY-ECHO.md`, prompt in `prompt-echo.txt`; search only, no shell, no `--dangerously-skip-permissions`). This is a digest with the claims we would act on, checked against our own measurements (`docs/BARGE-IN.md`, `docs/INTERRUPTS.md`). **Read "How far to trust it" before citing anything.**

## The one-paragraph answer

Nobody solves echo inside the voice-activity detector. Smart speakers cancel the echo in a dedicated DSP using the exact digital playback signal as a reference (plus microphone arrays, beamforming, residual-echo suppression, and ducking the volume when they suspect an interruption). Voice-agent frameworks and platforms push echo cancellation to the endpoint (the browser's WebRTC, the phone OS, a telephony gateway, a speakerphone) and tell developers to use a headset while developing; on top of that they apply guard rules (minimum speech duration, minimum recognised words, a start-of-playback grace period, a cooldown after an interruption) and truncate the conversation to the words actually spoken. This matches what we measured: with the assistant's voice leaking into the microphone, every software detector fails, and the Silero VAD fails worst because the assistant's voice is speech.

## What the commercial devices do

| Device | Echo handling | Where | Barge-in policy |
|---|---|---|---|
| Amazon Echo | circular multi-mic array (7), digital playback loopback as reference, linear adaptive filter + beamforming, ducking | on-device DSP | historically wake-word-only barge-in; now also device-directed speech detection |
| Google Nest | multichannel echo cancellation against the playback reference, beamforming, media ducking | on-device | wake word / quick phrases; volume ducks immediately on suspected speech |
| Apple HomePod / Siri | multichannel echo cancellation (linear) + deep-learning residual echo suppression + 6-mic beamforming | on the device's SoC | "Hey Siri" works with music 30-40 dB louder than speech at the microphones |
| Phones / browsers (ChatGPT Voice, Gemini Live clients) | OS or WebRTC acoustic echo cancellation (`echoCancellation: true`) | client | the server never sees the echo |

## What the frameworks do

| Framework | Echo cancellation | Interruption policy (as documented) |
|---|---|---|
| Pipecat | at the transport (Daily / WebRTC); docs recommend headphones locally | interruption frames, TTS flushed, only words actually heard committed to context |
| LiveKit Agents | client WebRTC; server-side Krisp / ai-coustics for SIP | `min_speech_duration` (about 150-300 ms), adaptive interruption model, TTS cancel under 200 ms, history truncated |
| Vapi | client / telephony | `stopSpeakingPlan`: `voiceSeconds` 0.2 s, `numWords` 0-3, `backoffSeconds` 1.0 s |
| Retell | client / telephony | interruption sensitivity slider, backchannel filtering |
| OpenAI Realtime | the client must do it; server VAD triggers falsely on echoed audio otherwise | `server_vad` threshold / prefix padding / silence duration; client truncates the played item |
| Deepgram Voice Agent | client; headset recommended in development | `UserStartedSpeaking`, client flushes its playback queue |
| Twilio Media Streams | none for room echo (telephony carrier does not do it) | server sends `clear` to purge Twilio's playback buffer |

## What is new for our setup

1. **Duck first, then stop.** At the first detection lower the volume (about 15 dB); cut only after about 200 ms of continued speech; restore if the speech stops. Main's ticket 12 already specifies exactly this (`barge_in_duck_db -15`, `min_barge_in_ms 250`, 15 ms fade). Our `doc_chat` stops at once (hard stop with a short fade). A false trigger then costs a dip in volume instead of a cut-off, which is the failure that mattered in our ablation.
2. **Speakerphone hardware cancellation only cancels what the speakerphone plays.** Play through the speakerphone *and* record through it, as one device. Playing out of the Pi's jack/HDMI and recording from the speakerphone defeats it. (Test condition C in `docs/PI-TEST-PLAN.md` exists to show this.)
3. **Clock drift and volume changes.** A microphone and a speaker on different clocks (laptop speakers + USB microphone) drift apart by tens of ppm and de-converge a software canceller over minutes; changing the volume mid-reply breaks convergence for a second or two.
4. **Start-of-playback guard.** Software cancellers need 0.5-2 s to converge; we measured an 11 dB leak on the first play. The report suggests a 250-300 ms guard; `docs/BARGE-IN.md` says ignore barge-in for 3 s "like LiveKit". These disagree: measure (the Pi `selftest` records `onsets_after_playback_start_s` for exactly this).
5. **PipeWire's cancellation also does noise suppression**, which is probably why the laptop's room floor dropped 12-17 dB. The module has options to switch noise suppression and gain control off (`webrtc.noise_suppression`, `webrtc.gain_control`, `webrtc.extended_filter`); **the exact key names were not verified** and the effect on recognition accuracy is **untested**.
6. **Text guard.** Matching the recognised words against the text the assistant is saying is a known technique; we built it (`looks_like_echo`). It cuts self-interruption loops 5-8x but cannot stop the first false stop.
7. **Speaker-aware options** (personalised VAD / target-speaker VAD, device-directed speech detection, DeepVQE and similar neural cancellers) exist but are laptop-class models; none can run on the Pi under the professor's rule.

## Hardware candidates for the Pi (prices are unverified guesses)

Seeed ReSpeaker XVF3800 (XMOS, 4-mic), Poly Sync 20, Jabra Speak2 55/75, Anker PowerConf S3/S330, eMeet M0/M2. Vendors advertise "full-duplex" and echo cancellation but rarely publish measured echo-return-loss figures. We do not know which device the project owns; the Pi test plan asks for the model.

## How far to trust it

- **Tagging is unreliable.** Many claims are marked "seen in a source" when they are the model's reasoning, and some simply repeat numbers from our own prompt (for example "directly matches your measurement (11 dB leak)"). Treat the tags as noise.
- **A citation is probably wrong.** DeepVQE is given as "ICASSP 2023, arXiv:2302.13327"; to our knowledge it is Interspeech 2023, arXiv:2306.03177. Check any reference before using it in a write-up.
- **Numbers are ranges, not data:** cancellation quality (20-40 dB), convergence (1-2 s), Raspberry Pi CPU use, prices, "ducks 12-20 dB". Use them to know what to measure, not as results.
- **What held up against our own data:** frameworks leave echo to the endpoint and recommend headsets; software VAD loops on open speakers; a start-of-playback leak exists; a 200 ms speech minimum and a word-count rule are the common interruption guards.
- **Not found:** the exact internal thresholds of Alexa / Google / Siri; measured echo-return-loss for consumer speakerphones; a packaged, fast Kalman-filter canceller for Python.

## What we do with it

The Pi test plan (`docs/PI-TEST-PLAN.md`) turns the open points into measurements: self-trigger rate of the Silero VAD on the real speakerphone, the first-play leak, the round-trip delay, and recognition accuracy during overlap. The decision between "hardware echo cancellation is enough" and "also add a software fallback" waits for those numbers.
